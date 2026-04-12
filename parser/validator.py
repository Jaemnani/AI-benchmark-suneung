"""
파싱된 JSON 데이터를 검증하고 보고서를 출력합니다.
"""
from typing import Any


def validate_problems(
    problems: list[dict],
    subject_config: dict,
) -> dict[str, Any]:
    """
    문제 배열을 검증하고 결과 보고서를 반환합니다.

    Returns:
        {
            "총_문제수": int,
            "기대_문제수": int,
            "누락_번호": list[int],
            "중복_번호": list[int],
            "선택지_오류": list[int],  # 5지선다형인데 선택지 ≠ 5개
            "정답_없음": list[int],
            "has_image_count": int,
            "valid": bool,
        }
    """
    total = subject_config.get("총_문제수", 0)
    subject_type = subject_config.get("유형", "")

    numbers = [p.get("번호") for p in problems if p.get("번호") is not None]
    number_set = set(numbers)

    # 중복 번호
    seen: set[int] = set()
    duplicates: list[int] = []
    for n in numbers:
        if n in seen:
            duplicates.append(n)
        seen.add(n)

    # 누락 번호 (국어는 1~45, 수학은 1~22 공통+선택 등 가변)
    if total > 0:
        expected = set(range(1, total + 1))
        missing = sorted(expected - number_set)
    else:
        missing = []

    # 5지선다형 선택지 개수 오류
    choice_errors: list[int] = []
    for p in problems:
        if p.get("유형") == "5지선다형":
            choices = p.get("선택지", [])
            if len(choices) != 5:
                choice_errors.append(p.get("번호", -1))

    # 정답 없음
    no_answer = [p.get("번호") for p in problems if p.get("정답") is None]

    # has_image 카운트
    has_image_count = sum(1 for p in problems if p.get("has_image"))

    is_valid = (
        len(duplicates) == 0
        and len(missing) == 0
        and len(choice_errors) == 0
    )

    return {
        "총_문제수": len(problems),
        "기대_문제수": total,
        "누락_번호": missing,
        "중복_번호": duplicates,
        "선택지_오류": choice_errors,
        "정답_없음": no_answer,
        "has_image_count": has_image_count,
        "valid": is_valid,
    }


def print_report(report: dict, subject_name: str) -> None:
    """검증 보고서를 콘솔에 출력합니다."""
    status = "✓ PASS" if report["valid"] else "✗ FAIL"
    print(f"\n{'='*50}")
    print(f"[검증 보고서] {subject_name}  {status}")
    print(f"{'='*50}")
    print(f"  추출 문제 수 : {report['총_문제수']}개  (기대: {report['기대_문제수']}개)")
    print(f"  이미지 포함  : {report['has_image_count']}개")

    if report["누락_번호"]:
        print(f"  ⚠ 누락 번호  : {report['누락_번호']}")
    else:
        print(f"  ✓ 번호 누락 없음")

    if report["중복_번호"]:
        print(f"  ⚠ 중복 번호  : {report['중복_번호']}")

    if report["선택지_오류"]:
        print(f"  ⚠ 선택지 오류 (5개 아님): 문제 {report['선택지_오류']}")
    else:
        print(f"  ✓ 선택지 개수 정상")

    if report["정답_없음"]:
        pct = len(report["정답_없음"]) / max(report["총_문제수"], 1) * 100
        print(f"  ⚠ 정답 미입력 : {len(report['정답_없음'])}개 ({pct:.0f}%)")
    else:
        print(f"  ✓ 전체 정답 입력 완료")

    print(f"{'='*50}\n")
