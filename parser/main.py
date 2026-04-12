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
from pathlib import Path

from config import SUBJECT_CONFIGS, OUTPUT_DIR
from pdf_converter import pdf_to_images
from vision_parser import parse_full_pdf
from answer_merger import parse_answer_key, merge_answers, merge_listening_script
from validator import validate_problems, print_report


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

    print(f"\n{'━'*55}")
    print(f" 처리 중: {subject_name} ({config['유형']})")
    print(f"{'━'*55}")

    # 출력 디렉토리 생성
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # ── 1단계: 문제지 PDF → PNG ─────────────────────────────────────────────
    print(f"[1/4] 문제지 PDF 변환...")
    try:
        question_images = pdf_to_images(config["문제지"])
    except FileNotFoundError as e:
        print(f"  [오류] {e}")
        return False

    # ── 2단계: Claude Vision 파싱 ───────────────────────────────────────────
    print(f"[2/4] Vision API 파싱 ({len(question_images)} 페이지)...")
    problems = parse_full_pdf(question_images, config, verbose=True)
    print(f"  → 총 {len(problems)}개 문제 추출")

    # ── 3단계: 정답표 파싱 및 병합 ─────────────────────────────────────────
    print(f"[3/4] 정답표 병합...")
    if os.path.exists(config.get("정답표", "")):
        answers = parse_answer_key(config["정답표"])
        problems = merge_answers(problems, answers, config)
    else:
        print(f"  [경고] 정답표 없음: {config.get('정답표', 'N/A')}")

    # 영어: 듣기 대본 병합
    if config.get("유형") == "영어" and config.get("듣기대본"):
        print(f"[3b] 듣기 대본 병합...")
        problems = merge_listening_script(problems, config["듣기대본"])

    # ── 4단계: 검증 및 저장 ─────────────────────────────────────────────────
    print(f"[4/4] 검증 및 저장...")
    report = validate_problems(problems, config)
    print_report(report, subject_name)

    # JSON 저장
    output_data = {
        "메타": {
            "영역": config.get("유형"),
            "과목명": subject_name,
            "연도": 2026,
            "총_문제수": len(problems),
            "valid": report["valid"],
        },
        "문제": problems,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"  → 저장 완료: {output_path}")
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

    # API 키 확인
    from config import ANTHROPIC_API_KEY
    if not ANTHROPIC_API_KEY:
        print("[오류] ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")
        print("  export ANTHROPIC_API_KEY='sk-ant-...'")
        sys.exit(1)

    # 처리할 과목 목록 결정
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

    # 처리 실행
    results: dict[str, bool] = {}
    for subject_key in subjects_to_process:
        success = process_subject(subject_key)
        results[subject_key] = success

    # 최종 요약
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
    print(f"{'═'*55}\n")


if __name__ == "__main__":
    main()
