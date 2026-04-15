"""
수능 정답표 PDF 파서.

- pdfplumber로 표 추출
- 각 페이지에 홀수형/짝수형이 구분 수록됨
- 표 구조: [문항번호, 정답, 배점] × N 컬럼 그룹
- 공통/선택 과목이 있으면(국어 등) 같은 번호가 선택 과목별로 중복될 수 있음
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

import pdfplumber


@dataclass
class AnswerEntry:
    number: int
    answer: str
    points: int | None
    section: str  # 공통, 화법과 작문, 언어와 매체, ...


@dataclass
class AnswerSheet:
    subject: str
    form: str  # 홀수형 or 짝수형
    source_pdf: str
    entries: list[AnswerEntry] = field(default_factory=list)


FORM_RE = re.compile(r"\(\s*(홀수|짝수)\s*\)\s*형")
NUM_RE = re.compile(r"^\d{1,3}$")
ANS_RE = re.compile(r"^[①②③④⑤]$|^\d{1,3}$")


def _detect_form(text: str) -> str:
    m = FORM_RE.search(text or "")
    if not m:
        return ""
    return f"{m.group(1)}형"


def _parse_section_headers(table: list[list[str | None]]) -> list[str]:
    """Return column→section label. Column groups are triples (번호,정답,배점).

    Only scan rows BEFORE the first data row (i.e., before any cell equals '1').
    """
    n_cols = len(table[0])
    n_groups = n_cols // 3
    labels = ["공통"] * n_groups
    header_rows: list[list[str | None]] = []
    for row in table:
        # stop at first data row — data rows have numeric cell at group position 0
        first_cell = (row[0] or "").strip() if row else ""
        if NUM_RE.match(first_cell):
            break
        header_rows.append(row)
    # Collect non-meta labels from header rows
    META = {"문항", "번호", "정답", "정 답", "배점", "배 점", "공통 과목", "선택 과목"}
    for g in range(n_groups):
        for row in header_rows:
            cell = row[g * 3] if g * 3 < len(row) else None
            if not cell:
                continue
            clean = cell.replace("\n", " ").strip()
            if not clean or clean in META or "문항" in clean:
                continue
            if "공통" in clean or "선택" in clean:
                continue
            labels[g] = clean
            break
    return labels


def _parse_table(table: list[list[str | None]]) -> list[tuple[int, str, int | None, int]]:
    """Yield (number, answer, points, group_index)."""
    out: list[tuple[int, str, int | None, int]] = []
    n_cols = len(table[0])
    n_groups = n_cols // 3
    for row in table:
        for g in range(n_groups):
            cells = row[g * 3 : g * 3 + 3]
            if len(cells) < 3:
                continue
            n, a, p = cells
            if not n or not a:
                continue
            n = n.strip()
            a = a.strip()
            if not NUM_RE.match(n) or not ANS_RE.match(a):
                continue
            try:
                pts = int(p.strip()) if p and p.strip() else None
            except ValueError:
                pts = None
            out.append((int(n), a, pts, g))
    return out


def parse_answer_sheet(pdf_path: Path, subject: str) -> list[AnswerSheet]:
    sheets: list[AnswerSheet] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            form = _detect_form(text)
            tables = page.extract_tables()
            if not tables:
                continue
            table = tables[0]
            labels = _parse_section_headers(table)
            sheet = AnswerSheet(subject=subject, form=form, source_pdf=str(pdf_path))
            for n, a, pts, g in _parse_table(table):
                sheet.entries.append(AnswerEntry(
                    number=n, answer=a, points=pts,
                    section=labels[g] if g < len(labels) else "공통",
                ))
            if sheet.entries:
                sheets.append(sheet)
    return sheets


def save_sheets(sheets: list[AnswerSheet], out_json: Path) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(
        [asdict(s) for s in sheets], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import sys
    pdf = Path(sys.argv[1])
    subject = sys.argv[2] if len(sys.argv) > 2 else pdf.stem
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("outputs/2025/answers")
    sheets = parse_answer_sheet(pdf, subject)
    save_sheets(sheets, out / f"{subject}.json")
    for s in sheets:
        print(f"[{subject} {s.form}] entries={len(s.entries)}")
