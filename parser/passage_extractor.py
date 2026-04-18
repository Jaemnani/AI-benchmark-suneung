"""
지문(passage) 경계 보정기 v3 — 텍스트 기반 필터링.

1. PyMuPDF 가 마커~문항 사이 텍스트 라인을 넉넉히 추출
2. Gemini 에 텍스트로 전달 → "지문 본문인 줄 번호만 반환"
3. 필터된 라인으로 텍스트 저장 + 이미지 crop

이미지 기반 경계 판별 대비:
- Gemini 가 레이아웃 해석 대신 텍스트 분류만 수행 → 정확
- 이미지 토큰 없이 텍스트만 전송 → 저렴

사용법:
  python passage_extractor.py                    # 전 과목
  python passage_extractor.py --subject 국어      # 국어만
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

import fitz  # PyMuPDF
from google import genai

# ─── 설정 ────────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get(
    "GEMINI_API_KEY",
    "AIzaSyB5seDl6Zn6L3SkjnATTfq6ZDX3G4eTSpg",
)
MODEL = "gemini-2.5-flash"
ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw_datas" / "2025"
OUT = ROOT / "outputs" / "2025"

client = genai.Client(api_key=GEMINI_API_KEY)

PASSAGE_MARKER_RE = re.compile(
    r"\[\s*(\d{1,2})\s*[~～∼\-–]\s*(\d{1,2})\s*\]"
)
Q_ANCHOR_RE = re.compile(r"^\s*(\d{1,2})\.\s")
CHOICE_START_RE = re.compile(r"^[①②③④⑤]")
EXCLUDE_KEYWORDS = {"국어 영역", "영어 영역", "수학 영역", "홀수형", "짝수형",
                     "제 1 교시", "제 2 교시", "제 3 교시", "제 4 교시", "제 5 교시",
                     "물음에 답하시오", "고르시오", "<보기>", "보 기"}


def _is_question_or_choice(text: str) -> bool:
    """문제 번호("N. ")나 선택지(①~⑤)로 시작하는 줄만 판별.
    지문 본문에 절대 나타나지 않는 패턴만 사용 — 오탐 방지."""
    t = text.strip()
    if not t:
        return False
    # 문제 번호: "N. " (줄 시작)
    if Q_ANCHOR_RE.match(t):
        return True
    # 선택지: ①②③④⑤ 시작
    if CHOICE_START_RE.match(t):
        return True
    return False


# ─── PyMuPDF 유틸 ────────────────────────────────────────────────────────────
def _get_page_lines(doc: fitz.Document, page_idx: int) -> list[dict]:
    """페이지의 모든 텍스트 라인을 reading order 로 반환."""
    page = doc[page_idx]
    mid_x = page.rect.width / 2
    lines = []
    d = page.get_text("dict")
    for b in d["blocks"]:
        if b["type"] != 0:
            continue
        for ln in b["lines"]:
            spans = ln["spans"]
            if not spans:
                continue
            text = "".join(s["text"] for s in spans).strip()
            if not text:
                continue
            bbox = ln["bbox"]
            col = 0 if bbox[0] < mid_x else 1
            lines.append({
                "text": text,
                "x0": bbox[0], "y0": bbox[1],
                "x1": bbox[2], "y1": bbox[3],
                "col": col, "page": page_idx,
            })
    lines.sort(key=lambda l: (l["col"], l["y0"], l["x0"]))
    return lines


def _find_passage_pages(doc: fitz.Document, start_n: int, end_n: int) -> list[int]:
    """마커 페이지 ~ 첫문항 페이지 (inclusive, 최대 3페이지).
    Gemini 텍스트 필터가 문제 라인을 걸러내므로 넉넉히 수집해도 안전."""
    half = doc.page_count // 2
    marker_page = None

    # 1) 마커 페이지 찾기
    for pi in range(half):
        text = doc[pi].get_text()
        for m in PASSAGE_MARKER_RE.finditer(text):
            if int(m.group(1)) == start_n and int(m.group(2)) == end_n:
                marker_page = pi
                break
        if marker_page is not None:
            break

    if marker_page is None:
        return []

    # 2) 첫 문항(start_n.) 페이지 찾기 (마커 이후)
    q_re = re.compile(rf"(?:^|\n)\s*{start_n}\.\s", re.MULTILINE)
    q_page = marker_page
    for pi in range(marker_page, min(marker_page + 4, half)):
        if q_re.search(doc[pi].get_text()):
            q_page = pi
            break

    # 3) 마커 ~ 첫문항 페이지 (inclusive), 최대 3페이지
    return list(range(marker_page, min(q_page + 1, marker_page + 4)))


def _collect_candidate_lines(doc: fitz.Document, pages: list[int],
                             start_n: int, end_n: int) -> list[dict]:
    """마커~첫문항 영역의 라인을 수집.
    - 마지막 페이지(첫문항 페이지)에서는 첫문항 y좌표 위쪽만 수집
    - 비지문 라인(문제/선택지/헤더)은 사전 필터링"""
    q_anchor_re = re.compile(rf"^\s*{start_n}\.\s")
    all_lines: list[dict] = []

    for pi, pg in enumerate(pages):
        page_lines = _get_page_lines(doc, pg)
        is_last_page = (pi == len(pages) - 1) and len(pages) > 1

        if is_last_page:
            # 마지막 페이지: 첫 문항 앵커의 y좌표 찾아서 그 위만 수집
            q_y = None
            for ln in page_lines:
                if q_anchor_re.match(ln["text"].strip()):
                    q_y = ln["y0"]
                    break
            if q_y is not None:
                page_lines = [l for l in page_lines if l["y0"] < q_y]

        all_lines.extend(page_lines)

    for i, ln in enumerate(all_lines):
        ln["idx"] = i
    return all_lines


# ─── Gemini 텍스트 필터링 ────────────────────────────────────────────────────
FILTER_PROMPT = """\
아래는 수능 시험지에서 [{start}~{end}] 지문 영역 주변의 텍스트 라인입니다.
각 라인 앞에 [번호]가 붙어 있습니다.

이 중에서 **[{start}~{end}] 지문의 본문에 해당하는 라인 번호만** JSON 배열로 반환해주세요.

■ 반드시 제외:
- 페이지 헤더 ("국어 영역", "홀수형", 페이지 번호 등)
- 지시문 ("[{start}~{end}] 다음 글을 읽고...", "물음에 답하시오" 등)
- 문제 (숫자+점으로 시작하는 줄: "10.", "35." 등)
- 선택지 (①②③④⑤로 시작하는 줄)
- <보기>, 각주(* 표시), 출처 표기
- [{start}~{end}] 범위가 아닌 다른 지문의 본문

■ 포함:
- 지문 본문 텍스트만 (글의 실제 내용)
- 지문 내 소제목, 인용문, 대화, 표제/각주 없는 본문

반환 형식 (JSON 배열만, 설명 없이):
[0, 1, 2, 5, 6, 7]

---
{lines_text}
"""


def _ask_filter(candidate_lines: list[dict], start_n: int, end_n: int) -> list[int]:
    """Gemini 에 텍스트를 보내 지문 라인 번호만 반환받는다."""
    lines_text = "\n".join(
        f"[{ln['idx']:>3}] {ln['text']}"
        for ln in candidate_lines
    )
    prompt = FILTER_PROMPT.format(
        start=start_n, end=end_n, lines_text=lines_text
    )

    for attempt in range(3):
        try:
            resp = client.models.generate_content(model=MODEL, contents=prompt)
            text = resp.text.strip()
            # JSON 배열 추출
            text = re.sub(r"```(?:json)?\s*", "", text)
            text = re.sub(r"```\s*$", "", text).strip()
            arr = json.loads(text)
            if isinstance(arr, list):
                return [int(x) for x in arr if isinstance(x, (int, float))]
        except json.JSONDecodeError:
            # [ ] 찾기
            s = text.find("[")
            e = text.rfind("]")
            if s != -1 and e > s:
                try:
                    arr = json.loads(text[s:e + 1])
                    return [int(x) for x in arr if isinstance(x, (int, float))]
                except json.JSONDecodeError:
                    pass
            if attempt < 2:
                time.sleep(0.5)
        except Exception as e:
            if attempt < 2:
                print(f"    [재시도 {attempt+1}] {e}")
                time.sleep(1)
            else:
                print(f"    [ERROR] {e}")
    return []


# ─── 이미지 crop ─────────────────────────────────────────────────────────────
def _crop_passage_images(doc: fitz.Document, body_lines: list[dict],
                         pid: str, img_dir: Path, out_dir: Path,
                         render_dpi: int = 200) -> list[str]:
    """body_lines 의 (page, col) 그룹별로 이미지를 crop."""
    col_groups: dict[tuple[int, int], list[dict]] = {}
    for ln in body_lines:
        col_groups.setdefault((ln["page"], ln["col"]), []).append(ln)

    image_paths: list[str] = []
    for (pg, col), gls in sorted(col_groups.items()):
        page = doc[pg]
        mid_x = page.rect.width / 2
        x0 = min(l["x0"] for l in gls) - 10
        y0 = min(l["y0"] for l in gls)
        x1 = max(l["x1"] for l in gls) + 10
        y1 = max(l["y1"] for l in gls)
        col_x1 = mid_x if col == 0 else page.rect.width

        # 이미지 블록 확장
        d_blocks = page.get_text("dict")["blocks"]
        for b in d_blocks:
            if b["type"] != 1:
                continue
            bx0, by0, bx1, by1 = b["bbox"]
            in_col = (bx0 < mid_x) if col == 0 else (bx0 >= mid_x)
            if in_col and by1 >= y0 - 10 and by0 <= y1 + 10:
                x0 = min(x0, bx0 - 5)
                x1 = max(x1, bx1 + 5)
                y0 = min(y0, by0)
                y1 = max(y1, by1)

        pad = 6
        clip = fitz.Rect(
            max(0, x0 - pad), max(0, y0 - pad),
            min(page.rect.width, min(x1, col_x1) + pad),
            min(page.rect.height, y1 + pad),
        )
        mat = fitz.Matrix(render_dpi / 72, render_dpi / 72)
        pix = page.get_pixmap(clip=clip, matrix=mat)
        img_name = f"passage_{pid}_p{pg + 1}c{col}.png"
        img_path = img_dir / img_name
        pix.save(img_path)
        image_paths.append(str(img_path.relative_to(out_dir)))

    return image_paths


# ─── 메인 처리 ───────────────────────────────────────────────────────────────
def process_passage(doc: fitz.Document, passage: dict, img_dir: Path,
                    out_dir: Path) -> bool:
    """단일 passage 를 텍스트 필터링 방식으로 보정."""
    qnums = passage["question_numbers"]
    start_n, end_n = qnums[0], qnums[-1]
    pid = passage["id"]

    pages = _find_passage_pages(doc, start_n, end_n)
    if not pages:
        print(f"페이지 못찾음")
        return False

    # 1) 후보 라인 수집
    candidates = _collect_candidate_lines(doc, pages, start_n, end_n)
    if not candidates:
        print(f"라인 없음")
        return False

    # 2) Gemini 텍스트 필터링
    kept_indices = _ask_filter(candidates, start_n, end_n)
    if not kept_indices:
        print(f"Gemini 필터 실패")
        return False

    idx_set = set(kept_indices)
    body_lines = [ln for ln in candidates if ln["idx"] in idx_set]

    # 코드 기반 후처리: 문제번호·선택지만 확실히 제거 (지문 본문 오탈 방지)
    body_lines = [ln for ln in body_lines if not _is_question_or_choice(ln["text"])]

    if not body_lines:
        print(f"필터 후 0줄")
        return False

    # 3) 텍스트 저장
    passage["text"] = "\n".join(ln["text"] for ln in body_lines)

    # 4) 이미지 crop
    passage["image_paths"] = _crop_passage_images(
        doc, body_lines, pid, img_dir, out_dir
    )
    passage["extraction_method"] = "gemini_text_filter+pymupdf"
    return True


def process_subject(subject_json: Path, pdf_path: Path) -> None:
    if not subject_json.exists() or not pdf_path.exists():
        print(f"  [스킵] 파일 없음")
        return

    data = json.loads(subject_json.read_text())
    passages = data.get("passages", [])
    if not passages:
        print(f"  [스킵] passage 없음")
        return

    doc = fitz.open(str(pdf_path))
    img_dir = OUT / "images" / subject_json.stem
    img_dir.mkdir(parents=True, exist_ok=True)

    # 기존 passage 이미지 삭제 (이전 실행 잔존 방지)
    for old_img in img_dir.glob("passage_*"):
        old_img.unlink()


    updated = 0
    for p in passages:
        pid = p["id"]
        print(f"    {pid}...", end=" ", flush=True)
        ok = process_passage(doc, p, img_dir, OUT)
        if ok:
            updated += 1
            n_imgs = len(p.get("image_paths", []))
            print(f"✓ {len(p['text'])}자 이미지{n_imgs}장")
        else:
            print("✗")
        time.sleep(0.3)

    doc.close()
    subject_json.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"  → {updated}/{len(passages)} 업데이트 → {subject_json}")


# ─── 과목 매핑 ───────────────────────────────────────────────────────────────
SUBJECT_PDF_MAP: dict[str, Path] = {
    "국어": RAW / "국어영역_문제지.pdf",
    "영어": RAW / "영어영역_문제지.pdf",
    "수학": RAW / "수학영역_문제지.pdf",
}
for grp, qdir in [("사회탐구", "사회탐구영역_문제지"), ("과학탐구", "과학탐구영역_문제지")]:
    d = RAW / qdir
    if d.is_dir():
        for pdf in sorted(d.glob("*.pdf")):
            name = pdf.stem.split(" ", 1)[-1].replace("_문제", "").strip()
            SUBJECT_PDF_MAP[f"{grp}_{name}"] = pdf


def main():
    ap = argparse.ArgumentParser(description="Gemini 텍스트필터 + PyMuPDF 지문추출")
    ap.add_argument("--subject", "-s")
    args = ap.parse_args()

    targets = {}
    for jf in sorted(OUT.glob("*.json")):
        if jf.name.startswith("_"):
            continue
        key = jf.stem
        pdf = SUBJECT_PDF_MAP.get(key)
        if pdf:
            targets[key] = (jf, pdf)

    if args.subject:
        if args.subject in targets:
            targets = {args.subject: targets[args.subject]}
        else:
            print(f"[오류] '{args.subject}' 없음. 가능: {list(targets.keys())}")
            return

    print(f"\n{'═'*55}")
    print(f" Gemini 텍스트필터 지문추출 ({MODEL})")
    print(f"{'═'*55}")

    for key, (jf, pdf) in targets.items():
        print(f"\n[{key}]")
        process_subject(jf, pdf)

    print(f"\n{'═'*55}\n 완료\n{'═'*55}\n")


if __name__ == "__main__":
    main()
