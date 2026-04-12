"""
2026 수능 시험지 PDF → JSON 파서
사용법:
  python main.py --subject 수학          # 수학영역만 처리
  python main.py --subject 과학탐구      # 과학탐구 전과목
  python main.py --all                   # 전체 처리
  python main.py --list                  # 사용 가능한 과목 목록 출력
"""
import argparse
import json
import os
import sys
from config import SUBJECT_CONFIGS, OUTPUT_DIR
from pdf_converter import pdf_to_images
from vision_parser import parse_full_pdf, parse_answer_key_page
from answer_merger import merge_answers, merge_listening_script
from validator import validate_problems, print_report
from viewer import generate_viewer, generate_group_viewer


# ─── 영역별 과목 그룹 ────────────────────────────────────────────────────────
SUBJECT_GROUPS = {
    "수학":       ["수학"],
    "국어":       ["국어"],
    "영어":       ["영어"],
    "한국사":     ["한국사"],
    "과학탐구":   ["물리학1", "화학1", "생명과학1", "지구과학1",
                   "물리학2", "화학2", "생명과학2", "지구과학2"],
    "사회탐구":   ["생활과윤리", "윤리와사상", "한국지리", "세계지리",
                   "동아시아사", "세계사", "경제", "정치와법", "사회문화"],
    "제2외국어":  ["독일어1", "프랑스어1", "스페인어1", "중국어1",
                   "일본어1", "러시아어1", "아랍어1", "베트남어1", "한문1"],
    "직업탐구":   ["성공적인직업생활", "농업기초기술", "공업일반",
                   "상업경제", "수산해운산업기초", "인간발달"],
}

# 수학 선택과목 목록
MATH_SELECTIONS = ["확률과통계", "미적분", "기하"]

# 그룹 통합 뷰어가 필요한 그룹명 → (영역명, 출력 디렉토리)
GROUP_VIEWER_CONFIGS: dict[str, tuple[str, str]] = {
    "과학탐구":  ("과학탐구영역",  os.path.join(OUTPUT_DIR, "과학탐구영역")),
    "사회탐구":  ("사회탐구영역",  os.path.join(OUTPUT_DIR, "사회탐구영역")),
    "제2외국어": ("제2외국어한문영역", os.path.join(OUTPUT_DIR, "제2외국어한문영역")),
    "직업탐구":  ("직업탐구영역",  os.path.join(OUTPUT_DIR, "직업탐구영역")),
}


def _make_log_dir(subject_key: str) -> str:
    """과목별 로그 디렉토리 경로를 반환하고 생성합니다."""
    log_dir = os.path.join(OUTPUT_DIR, "logs", subject_key)
    os.makedirs(os.path.join(log_dir, "images"), exist_ok=True)
    os.makedirs(os.path.join(log_dir, "api_responses"), exist_ok=True)
    os.makedirs(os.path.join(log_dir, "errors"), exist_ok=True)
    return log_dir


def _save_json(data: dict | list, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  → 저장: {path}")


def save_math_output(problems: list[dict], _config: dict) -> None:
    """
    수학 문제를 홀수형/짝수형 × 공통/선택과목별로 분리 저장합니다.
    output/2026/수학영역/
        홀수형.json
        짝수형.json
    각 파일 내부:
        {
          "형태": "홀수형",
          "공통": [...],
          "선택과목": {
              "확률과통계": [...],
              "미적분": [...],
              "기하": [...]
          }
        }
    """
    base_dir = os.path.join(OUTPUT_DIR, "수학영역")
    os.makedirs(base_dir, exist_ok=True)

    # 형태별로 분리
    by_form: dict[str, list[dict]] = {}
    for p in problems:
        form = p.get("형태", "홀수형")
        by_form.setdefault(form, []).append(p)

    for form, form_problems in by_form.items():
        공통 = sorted(
            [p for p in form_problems if not p.get("선택과목")],
            key=lambda p: p.get("번호", 0),
        )
        선택 = {
            subj: sorted(
                [p for p in form_problems if p.get("선택과목명") == subj],
                key=lambda p: p.get("번호", 0),
            )
            for subj in MATH_SELECTIONS
        }

        output_data = {
            "메타": {
                "영역": "수학",
                "형태": form,
                "연도": 2026,
                "공통_문제수": len(공통),
                "선택과목_문제수": {subj: len(probs) for subj, probs in 선택.items()},
            },
            "공통": 공통,
            "선택과목": 선택,
        }

        path = os.path.join(base_dir, f"{form}.json")
        _save_json(output_data, path)

    # 형태 감지 실패(형태 필드 없음) 문제가 있으면 별도 저장
    unknown = [p for p in problems if not p.get("형태")]
    if unknown:
        _save_json({"메타": {"note": "형태 미감지 문제"}, "문제": unknown},
                   os.path.join(base_dir, "형태미감지.json"))
        print(f"  ⚠ 형태 미감지 문제 {len(unknown)}개 → 형태미감지.json")


def process_subject(subject_key: str) -> bool:
    """
    단일 과목을 처리합니다.
    Returns True if successful.
    """
    if subject_key not in SUBJECT_CONFIGS:
        print(f"[오류] 알 수 없는 과목: {subject_key}")
        return False

    config = SUBJECT_CONFIGS[subject_key]
    subject_name = config.get("과목명", subject_key)
    output_path = config["출력"]
    subject_type = config["유형"]

    print(f"\n{'━'*55}")
    print(f" 처리 중: {subject_name} ({subject_type})")
    print(f"{'━'*55}")

    # 출력 디렉토리 및 로그 디렉토리 생성
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    log_dir = _make_log_dir(subject_key)
    print(f"  로그 저장 위치: {log_dir}")

    # ── 1단계: 문제지 PDF → PNG ─────────────────────────────────────────────
    print(f"\n[1/4] 문제지 PDF 변환...")
    try:
        question_images = pdf_to_images(config["문제지"])
    except FileNotFoundError as e:
        print(f"  [오류] {e}")
        return False

    # ── 2단계: Claude Vision 파싱 ───────────────────────────────────────────
    print(f"\n[2/4] Vision API 파싱 ({len(question_images)} 페이지)...")
    problems = parse_full_pdf(question_images, config, verbose=True, log_dir=log_dir)
    print(f"  → 총 {len(problems)}개 문제 추출")

    # ── 3단계: 정답표 파싱 및 병합 ─────────────────────────────────────────
    print(f"\n[3/4] 정답표 병합...")
    answer_pdf = config.get("정답표", "")
    if answer_pdf and os.path.exists(answer_pdf):
        answer_log_dir = log_dir  # 정답표 이미지도 같은 로그 폴더에
        answer_images = pdf_to_images(answer_pdf)
        all_answers: dict = {}
        for i, img in enumerate(answer_images, start=1):
            page_answers = parse_answer_key_page(img, i, log_dir=answer_log_dir)
            all_answers.update(page_answers)
        print(f"  → {len(all_answers)}개 정답 추출")
        problems = merge_answers(problems, all_answers, config)
    else:
        print(f"  [경고] 정답표 없음: {answer_pdf or 'N/A'}")

    # 영어: 듣기 대본 병합
    if subject_type == "영어" and config.get("듣기대본"):
        print(f"\n[3b] 듣기 대본 병합...")
        problems = merge_listening_script(problems, config["듣기대본"])

    # ── 4단계: 검증 및 저장 ─────────────────────────────────────────────────
    print(f"\n[4/4] 검증 및 저장...")
    report = validate_problems(problems, config)
    print_report(report, subject_name)

    # 수학: 홀수/짝수형 분리 저장
    if subject_type == "수학":
        save_math_output(problems, config)
    else:
        output_data = {
            "메타": {
                "영역": subject_type,
                "과목명": subject_name,
                "연도": 2026,
                "총_문제수": len(problems),
                "valid": report["valid"],
            },
            "문제": problems,
        }
        _save_json(output_data, output_path)

    # 검증 보고서도 로그로 저장
    report_path = os.path.join(log_dir, "validation_report.json")
    _save_json({**report, "과목명": subject_name}, report_path)

    # ── 5단계: HTML 뷰어 자동 생성 ──────────────────────────────────────────
    print(f"\n[5/5] 뷰어 생성...")
    viewer_path = generate_viewer(config, output_path)
    if viewer_path:
        print(f"  → 뷰어: {viewer_path}")
    else:
        print(f"  → 뷰어 생성 건너뜀 (출력 파일 없음)")

    return True


def list_subjects() -> None:
    """사용 가능한 과목 목록을 출력합니다."""
    print("\n사용 가능한 과목 키:")
    print()
    for group, subjects in SUBJECT_GROUPS.items():
        print(f"  --subject {group:<12} (그룹: {', '.join(subjects)})")
    print()
    print("  개별 과목 키:")
    for key, cfg in SUBJECT_CONFIGS.items():
        name = cfg.get("과목명", key)
        print(f"    {key:<20} → {name}")


def main():
    parser = argparse.ArgumentParser(
        description="2026 수능 시험지 PDF → JSON 파서",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--subject", "-s", help="처리할 과목 (그룹 또는 개별 키)")
    parser.add_argument("--all", "-a", action="store_true", help="모든 과목 처리")
    parser.add_argument("--list", "-l", action="store_true", help="과목 목록 출력")

    args = parser.parse_args()

    if args.list:
        list_subjects()
        return

    from config import ANTHROPIC_API_KEY
    if not ANTHROPIC_API_KEY:
        print("[오류] ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")
        print("  export ANTHROPIC_API_KEY='sk-ant-...'")
        sys.exit(1)

    subjects_to_process: list[str] = []

    if args.all:
        subjects_to_process = list(SUBJECT_CONFIGS.keys())
    elif args.subject:
        key = args.subject
        if key in SUBJECT_GROUPS:
            subjects_to_process = SUBJECT_GROUPS[key]
        elif key in SUBJECT_CONFIGS:
            subjects_to_process = [key]
        else:
            print(f"[오류] 알 수 없는 과목: '{key}'")
            print("  --list 옵션으로 사용 가능한 과목을 확인하세요.")
            sys.exit(1)
    else:
        parser.print_help()
        return

    results: dict[str, bool] = {}
    for subject_key in subjects_to_process:
        success = process_subject(subject_key)
        results[subject_key] = success

    print(f"\n{'═'*55}")
    print(" 처리 결과 요약")
    print(f"{'═'*55}")
    success_count = sum(1 for v in results.values() if v)
    for key, ok in results.items():
        name = SUBJECT_CONFIGS[key].get("과목명", key)
        status = "✓" if ok else "✗"
        print(f"  {status} {name}")
    print(f"{'─'*55}")
    print(f"  {success_count}/{len(results)} 완료")
    print(f"  출력 디렉토리: {OUTPUT_DIR}")
    print(f"  로그 디렉토리: {os.path.join(OUTPUT_DIR, 'logs')}")

    # ── 그룹 통합 뷰어 생성 ────────────────────────────────────────────────
    # 이번 실행에서 처리된 과목은 어떤 그룹에 속하는지 확인
    processed_groups: set[str] = set()
    for subject_key in subjects_to_process:
        for group_key, group_subjects in SUBJECT_GROUPS.items():
            if subject_key in group_subjects and group_key in GROUP_VIEWER_CONFIGS:
                processed_groups.add(group_key)

    if processed_groups:
        print(f"\n[그룹 통합뷰어] 생성 중...")
        for group_key in sorted(processed_groups):
            group_area_name, group_output_dir = GROUP_VIEWER_CONFIGS[group_key]
            group_subject_keys = SUBJECT_GROUPS[group_key]
            group_subjects_info = [
                (k, SUBJECT_CONFIGS[k], SUBJECT_CONFIGS[k]["출력"])
                for k in group_subject_keys
                if k in SUBJECT_CONFIGS
            ]
            viewer_path = generate_group_viewer(
                group_area_name,
                group_subjects_info,
                group_output_dir,
            )
            if viewer_path:
                print(f"  ✓ {group_area_name} 그룹 뷰어: {viewer_path}")
            else:
                print(f"  ⚠ {group_area_name}: 생성 가능한 과목 데이터 없음")

    print(f"{'═'*55}\n")


if __name__ == "__main__":
    main()
