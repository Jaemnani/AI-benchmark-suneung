"""
수능 문제지 PDF 파서.

- PyMuPDF로 텍스트/bbox 추출
- 2단 레이아웃은 x 중앙값으로 좌/우 컬럼 분리
- 문제 번호 anchor: 각 라인의 첫 span이 `^\d{1,2}\.\s*$` 또는 `^\d{1,2}\.\s+...` 로 시작
- 문항별 bbox 영역을 PNG로 렌더(설명 이미지/수식/도표 보존용)
- 객관식은 ①②③④⑤ 기준 split
"""
from __future__ import annotations
import json
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from pathlib import Path

import fitz  # PyMuPDF

CHOICE_MARKS = "①②③④⑤"
CHOICE_RE = re.compile(f"[{CHOICE_MARKS}]")
Q_ANCHOR_RE = re.compile(r"^\s*(\d{1,2})\.\s*$|^\s*(\d{1,2})\.\s+\S")
PASSAGE_RE = re.compile(r"^\s*\[\s*(\d{1,2})\s*[~～∼\-–]\s*(\d{1,2})\s*\]")
SECTION_HEADER_RE = re.compile(r"^\s*\(\s*([^)]+?)\s*\)\s*$")
SECTION_FONT_MIN = 25.0  # 선택과목 헤더 (예: "(화법과 작문)") fs ≈ 26~31
FORM_FONT_MIN = 20.0     # 페이지 상단 "홀수형/짝수형" 식별 fs ≈ 25.8
DEFAULT_SECTION = "공통"
HEADER_Y_RATIO = 0.11  # 상단 헤더. 수학 미적분 Q26 이 y≈141 에 있어 0.12는 공격적.
FOOTER_Y_RATIO = 0.90  # 하단 푸터(페이지번호, 저작권)


@dataclass
class Line:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    first_span: str
    page: int
    column: int
    font_size: float = 0.0


@dataclass
class Question:
    number: int
    page: int
    column: int
    bbox: tuple[float, float, float, float]
    question: str
    choices: list[str] = field(default_factory=list)
    image_path: str | None = None
    passage_id: str | None = None
    section: str = DEFAULT_SECTION


@dataclass
class Passage:
    id: str
    question_numbers: list[int]
    intro: str  # marker line text (e.g. "[1~3] 다음 글을 읽고 물음에 답하시오.")
    text: str
    image_paths: list[str] = field(default_factory=list)


@dataclass
class ParsedPaper:
    subject: str
    form: str
    source_pdf: str
    questions: list[Question] = field(default_factory=list)
    passages: list[Passage] = field(default_factory=list)


def _extract_lines(doc: fitz.Document) -> list[Line]:
    out: list[Line] = []
    for pi, page in enumerate(doc):
        mid_x = page.rect.width / 2
        page_h = page.rect.height
        header_y = page_h * HEADER_Y_RATIO
        footer_y = page_h * FOOTER_Y_RATIO
        d = page.get_text("dict")
        for b in d["blocks"]:
            if b["type"] != 0:
                continue
            for l in b["lines"]:
                spans = l["spans"]
                if not spans:
                    continue
                text = "".join(s["text"] for s in spans)
                if not text.strip():
                    continue
                x0, y0, x1, y1 = l["bbox"]
                # drop header/footer zones
                if y0 < header_y or y0 > footer_y:
                    continue
                col = 0 if x0 < mid_x else 1
                out.append(Line(
                    x0=x0, y0=y0, x1=x1, y1=y1,
                    text=text,
                    first_span=spans[0]["text"],
                    page=pi,
                    column=col,
                ))
    return out


def _detect_form(doc: fitz.Document) -> str:
    first = doc[0].get_text()
    for key in ("홀수형", "짝수형"):
        if key in first:
            return key
    return ""


def _build_page_segments(doc: fitz.Document) -> list[dict]:
    """각 페이지가 속한 (form, section)을 결정한다.

    페이지 상단에 본문 폰트보다 뚜렷이 큰 헤더가 있다:
      - `홀수형` / `짝수형` (form)
      - `(화법과 작문)`, `(확률과 통계)` 등 (선택과목 section)
    form 이 짝수형으로 바뀌면 section 은 다시 공통으로 리셋된다.
    """
    n = doc.page_count
    meta: list[dict] = [None] * n  # type: ignore
    cur_form = "홀수형"
    cur_section = DEFAULT_SECTION
    for pi in range(n):
        page = doc[pi]
        new_form = cur_form
        new_section: str | None = None
        d = page.get_text("dict")
        for b in d["blocks"]:
            if b["type"] != 0:
                continue
            for line in b["lines"]:
                spans = line["spans"]
                if not spans:
                    continue
                text = "".join(s["text"] for s in spans).strip()
                if not text:
                    continue
                fs = max(s["size"] for s in spans)
                if fs >= FORM_FONT_MIN and text in ("홀수형", "짝수형"):
                    new_form = text
                if fs >= SECTION_FONT_MIN:
                    m = SECTION_HEADER_RE.match(text)
                    if m:
                        new_section = m.group(1).strip()
        if new_form != cur_form:
            # form 전환 시 선택과목이 다시 공통부터 시작
            cur_section = DEFAULT_SECTION
        cur_form = new_form
        if new_section is not None:
            cur_section = new_section
        meta[pi] = {"form": cur_form, "section": cur_section}
    return meta


def _sort_reading_order(lines: list[Line]) -> list[Line]:
    return sorted(lines, key=lambda l: (l.page, l.column, l.y0, l.x0))


def _is_question_anchor(line: Line) -> int | None:
    # Some PDFs prepend a whitespace span; check first_span first, fall back to full line text
    for candidate in (line.first_span, line.text):
        m = Q_ANCHOR_RE.match(candidate)
        if m:
            num = m.group(1) or m.group(2)
            return int(num) if num else None
    return None


def _split_question_text(body: str) -> tuple[str, list[str]]:
    idxs = [(m.start(), m.group()) for m in CHOICE_RE.finditer(body)]
    if not idxs:
        return body.strip(), []
    stem = body[: idxs[0][0]].strip()
    choices: list[str] = []
    for i, (pos, _) in enumerate(idxs):
        end = idxs[i + 1][0] if i + 1 < len(idxs) else len(body)
        chunk = body[pos + 1 : end].strip()
        choices.append(chunk)
    return stem, choices


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def parse_paper(pdf_path: Path, out_dir: Path, subject: str, render_dpi: int = 200) -> ParsedPaper:
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    img_dir = out_dir / "images" / subject
    img_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    form = _detect_form(doc)
    page_meta = _build_page_segments(doc)
    lines = _sort_reading_order(_extract_lines(doc))

    # 홀수형 페이지의 라인만 사용 (짝수형은 내용이 동일하므로 스킵)
    lines = [l for l in lines if page_meta[l.page]["form"] == "홀수형"]

    # anchor 수집 + 섹션 태깅
    raw_anchors: list[tuple[int, Line, str]] = []
    for line in lines:
        q = _is_question_anchor(line)
        if q is not None:
            raw_anchors.append((q, line, page_meta[line.page]["section"]))

    # 섹션별 monotonic dedup (false positive 제거)
    anchors: list[tuple[int, Line, str]] = []
    last_by_section: dict[str, int] = {}
    for num, ln, sec in raw_anchors:
        if num > last_by_section.get(sec, 0):
            anchors.append((num, ln, sec))
            last_by_section[sec] = num

    line_to_idx = {id(l): i for i, l in enumerate(lines)}
    anchor_idx_by_num: dict[int, int] = {
        num: line_to_idx[id(ln)] for num, ln, _sec in anchors
    }

    # Detect passage markers and build passages
    # 핵심: body_lines 는 마커와 같은 section + 마커~첫문항 사이만 수집
    passages: list[Passage] = []
    question_to_passage: dict[int, str] = {}
    seen_ranges: set[tuple[int, int, str]] = set()  # (start, end, section)

    # 마커 라인에 section 태깅
    for i, ln in enumerate(lines):
        m = PASSAGE_RE.match(ln.text)
        if not m:
            continue
        start_n, end_n = int(m.group(1)), int(m.group(2))
        marker_sec = page_meta[ln.page]["section"]

        # 같은 (범위, 섹션) 중복 방지
        if (start_n, end_n, marker_sec) in seen_ranges:
            continue
        seen_ranges.add((start_n, end_n, marker_sec))

        pid = f"p{start_n}-{end_n}"
        # 섹션이 여러 개면 pid 에 섹션 구분자 추가
        if marker_sec != DEFAULT_SECTION:
            pid = f"p{start_n}-{end_n}_{marker_sec}"

        q_idx = anchor_idx_by_num.get(start_n)
        if q_idx is None or q_idx <= i:
            # body 없는 경우 (듣기/지시문 공유형)
            passages.append(Passage(
                id=pid,
                question_numbers=list(range(start_n, end_n + 1)),
                intro=_normalize(ln.text),
                text="",
                image_paths=[],
            ))
            for qn in range(start_n, end_n + 1):
                question_to_passage[qn] = pid
            continue

        # body_lines: 마커~첫문항 사이, 같은 section 만 (다단·다페이지 허용)
        body_lines = [
            l for l in lines[i + 1 : q_idx]
            if page_meta[l.page]["section"] == marker_sec
        ]

        # 이미지 렌더링: page+column 단위 (다른 컬럼의 비지문 콘텐츠 혼입 방지)
        col_groups: dict[tuple[int, int], list[Line]] = {}
        for bl in body_lines:
            col_groups.setdefault((bl.page, bl.column), []).append(bl)
        image_paths: list[str] = []
        for (pg, col), gls in sorted(col_groups.items()):
            page = doc[pg]
            mid_x = page.rect.width / 2
            gx0 = min(l.x0 for l in gls) - 10
            gx1 = mid_x if col == 0 else page.rect.width
            gy0 = min(l.y0 for l in gls)
            gy1 = max(l.y1 for l in gls)
            # 같은 컬럼·y범위 내 이미지 블록 확장
            d_blocks = page.get_text("dict")["blocks"]
            for b in d_blocks:
                if b["type"] != 1:
                    continue
                bx0, by0, bx1, by1 = b["bbox"]
                in_col = (bx0 < mid_x) if col == 0 else (bx0 >= mid_x)
                if in_col and by1 >= gy0 - 10 and by0 <= gy1 + 10:
                    gx0 = min(gx0, bx0 - 10)
                    gy0 = min(gy0, by0)
                    gy1 = max(gy1, by1)
            pad = 6
            clip = fitz.Rect(
                max(0, gx0 - pad), max(0, gy0 - pad),
                min(page.rect.width, gx1 + pad),
                min(page.rect.height, gy1 + pad),
            )
            mat = fitz.Matrix(render_dpi / 72, render_dpi / 72)
            pix = page.get_pixmap(clip=clip, matrix=mat)
            img_name = f"passage_{pid}_p{pg + 1}c{col}.png"
            img_path = img_dir / img_name
            pix.save(img_path)
            image_paths.append(str(img_path.relative_to(out_dir)))

        body_text = _normalize("\n".join(l.text for l in body_lines))
        passages.append(Passage(
            id=pid,
            question_numbers=list(range(start_n, end_n + 1)),
            intro=_normalize(ln.text),
            text=body_text,
            image_paths=image_paths,
        ))
        for qn in range(start_n, end_n + 1):
            question_to_passage[qn] = pid

    questions: list[Question] = []
    for i, (num, anchor, sec) in enumerate(anchors):
        start = line_to_idx[id(anchor)]
        end = line_to_idx[id(anchors[i + 1][1])] if i + 1 < len(anchors) else len(lines)
        qlines = lines[start:end]
        # Restrict body TEXT to anchor's own page + column
        same = [l for l in qlines if l.page == anchor.page and l.column == anchor.column]
        if not same:
            same = [anchor]

        body = "\n".join(l.text for l in same)
        body = re.sub(rf"^\s*{num}\.\s*", "", body)
        stem, choices = _split_question_text(body)
        stem = _normalize(stem)
        choices = [_normalize(c) for c in choices]

        # 이미지 crop: tight left(앵커 x0 기준) + tight bottom(콘텐츠 끝 기준)
        page = doc[anchor.page]
        mid_x = page.rect.width / 2
        content_x0 = anchor.x0 - 10
        col_x1 = mid_x if anchor.column == 0 else page.rect.width
        crop_y0 = anchor.y0

        # 다음 앵커 y (상한)
        if i + 1 < len(anchors):
            next_anc = anchors[i + 1][1]
            if next_anc.page == anchor.page and next_anc.column == anchor.column:
                max_y1 = next_anc.y0 - 2
            else:
                max_y1 = page.rect.height * FOOTER_Y_RATIO
        else:
            max_y1 = page.rect.height * FOOTER_Y_RATIO

        # 콘텐츠 끝점: 텍스트 + 이미지 블록을 통합하여 연속 클러스터의 하단 탐지
        # 1) 이 문항 영역 내의 모든 콘텐츠 y범위 수집 (텍스트 라인 + 이미지 블록)
        content_bands: list[tuple[float, float]] = []
        for l in same:
            content_bands.append((l.y0, l.y1))
        d_blocks = page.get_text("dict")["blocks"]
        for b in d_blocks:
            if b["type"] != 1:
                continue
            bx0, by0, bx1, by1 = b["bbox"]
            if bx0 < col_x1 and bx1 > content_x0 and by0 >= crop_y0 and by1 <= max_y1:
                content_bands.append((by0, by1))
        content_bands.sort()

        # 2) 연속 클러스터: gap > threshold 이면 중단
        GAP_THRESHOLD = 50
        content_y1 = content_bands[0][1] if content_bands else crop_y0
        for cy0, cy1 in content_bands:
            if cy0 - content_y1 > GAP_THRESHOLD:
                break
            content_y1 = max(content_y1, cy1)

        crop_y1 = min(content_y1 + 15, max_y1)

        pad = 4
        top_pad = 10  # 수식 분자·지수가 bbox 위로 튀어나오므로 상단 여백 확보
        clip = fitz.Rect(
            max(0, content_x0 - pad), max(0, crop_y0 - top_pad),
            min(page.rect.width, col_x1 + pad),
            min(page.rect.height, crop_y1 + pad),
        )
        mat = fitz.Matrix(render_dpi / 72, render_dpi / 72)
        pix = page.get_pixmap(clip=clip, matrix=mat)
        img_name = f"q{num:02d}.png"
        img_path = img_dir / img_name
        pix.save(img_path)

        x0 = min(l.x0 for l in same)
        y0 = min(l.y0 for l in same)
        x1 = max(l.x1 for l in same)
        y1 = max(l.y1 for l in same)

        questions.append(Question(
            number=num,
            page=anchor.page + 1,
            column=anchor.column,
            bbox=(round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)),
            question=stem,
            choices=choices,
            image_path=str(img_path.relative_to(out_dir)),
            passage_id=question_to_passage.get(num),
            section=sec,
        ))

    return ParsedPaper(
        subject=subject,
        form=form,
        source_pdf=str(pdf_path),
        questions=questions,
        passages=passages,
    )


def save_parsed(paper: ParsedPaper, out_json: Path) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    d = asdict(paper)
    out_json.write_text(json.dumps(d, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import sys
    pdf = Path(sys.argv[1])
    subject = sys.argv[2] if len(sys.argv) > 2 else pdf.stem
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("outputs/2025")
    paper = parse_paper(pdf, out, subject)
    save_parsed(paper, out / f"{subject}.json")
    print(f"[{subject}] form={paper.form} questions={len(paper.questions)}")
