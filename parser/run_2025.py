"""2025 수능 5개 영역 일괄 파싱 + 자체 검증."""
from __future__ import annotations
import json
from pathlib import Path

from ksat_parser import parse_paper, save_parsed
from ksat_answer_parser import parse_answer_sheet, save_sheets

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw_datas" / "2025"
OUT = ROOT / "outputs" / "2025"

SINGLE = [
    ("국어", "국어영역_문제지.pdf", "국어영역_정답표.pdf"),
    ("수학", "수학영역_문제지.pdf", "수학영역_정답표.pdf"),
    ("영어", "영어영역_문제지.pdf", "영어영역_정답표.pdf"),
]

GROUPED = [
    ("사회탐구", "사회탐구영역_문제지", "사회탐구영역_정답표"),
    ("과학탐구", "과학탐구영역_문제지", "과학탐구영역_정답표"),
]


def run_one(key: str, qp: Path, ap: Path) -> dict:
    paper = parse_paper(qp, OUT, key)
    save_parsed(paper, OUT / f"{key}.json")
    sheets = parse_answer_sheet(ap, key)
    save_sheets(sheets, OUT / "answers" / f"{key}.json")

    # Self-check: questions should be 1..N contiguous
    nums = sorted(q.number for q in paper.questions)
    contiguous = nums == list(range(1, len(nums) + 1)) if nums else False
    # Each question: choices either 0 (단답형) or 5
    choice_counts: dict[int, int] = {}
    for q in paper.questions:
        choice_counts[len(q.choices)] = choice_counts.get(len(q.choices), 0) + 1
    # Cross-check with answer sheet (홀수형)
    ans_odd = next((s for s in sheets if s.form == "홀수형"), None)
    if ans_odd is None and sheets:
        ans_odd = sheets[0]  # 탐구영역: 홀/짝 구분 없음
    ans_count = len(ans_odd.entries) if ans_odd else 0

    return {
        "key": key,
        "form": paper.form,
        "n_questions": len(paper.questions),
        "contiguous": contiguous,
        "choice_histogram": choice_counts,
        "n_passages": len(paper.passages),
        "n_answers_odd": ans_count,
    }


def main() -> None:
    results = []
    for subj, q, a in SINGLE:
        results.append(run_one(subj, RAW / q, RAW / a))
    for grp, qd, ad in GROUPED:
        qdir = RAW / qd
        adir = RAW / ad
        q_files = sorted(qdir.glob("*.pdf"))
        a_files = sorted(adir.glob("*.pdf"))

        def _subject_name(stem: str) -> str:
            part = stem.split(" ", 1)[-1] if " " in stem else stem
            return part.replace("_문제", "").replace("_정답", "").strip()

        a_map = {_subject_name(p.stem): p for p in a_files}
        for qp in q_files:
            name = _subject_name(qp.stem)
            key = f"{grp}_{name}"
            ap = a_map.get(name)
            if not ap:
                print(f"[skip] no answer for {key}")
                continue
            results.append(run_one(key, qp, ap))

    print("\n=== self-test summary ===")
    print(f"{'subject':<28}{'form':<8}{'N':>4}{'cont':>6}{'pass':>6}{'Aodd':>6}  choices")
    for r in results:
        print(f"{r['key']:<28}{r['form']:<8}{r['n_questions']:>4}"
              f"{'Y' if r['contiguous'] else 'N':>6}"
              f"{r['n_passages']:>6}{r['n_answers_odd']:>6}  {r['choice_histogram']}")

    # Save summary
    (OUT / "_self_test.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
