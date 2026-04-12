"""
Claude Vision API를 사용하여 시험지 페이지를 파싱합니다.
"""
import json
import os
import re
import time
from typing import Any

import anthropic
from PIL import Image

from config import ANTHROPIC_API_KEY, MODEL_SONNET, PROMPT_DIR
from pdf_converter import image_to_base64

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def load_prompt(prompt_file: str) -> str:
    """prompts/ 디렉토리에서 프롬프트 파일을 읽어 반환합니다."""
    path = os.path.join(PROMPT_DIR, prompt_file)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _build_system_prompt(subject_config: dict) -> str:
    """과목 설정을 바탕으로 system 프롬프트를 구성합니다."""
    base = load_prompt(subject_config["프롬프트"])

    extras = []
    if "과목명" in subject_config:
        extras.append(f"Subject: {subject_config['과목명']}")
    if "언어" in subject_config:
        extras.append(f"Language: {subject_config['언어']}")
    if subject_config.get("방향") == "RTL":
        extras.append('Text direction: RTL (Right-to-Left). Add "방향": "RTL" to each problem.')

    if extras:
        base = "\n".join(extras) + "\n\n" + base

    return base


def _extract_json_from_response(text: str) -> Any:
    """Claude 응답에서 JSON 부분만 추출합니다."""
    text = text.strip()
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```\s*$", "", text)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for start_char, end_char in [("[", "]"), ("{", "}")]:
            start = text.find(start_char)
            end = text.rfind(end_char)
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue

        raise ValueError(f"Failed to extract JSON from response:\n{text[:500]}")


def parse_page(
    image: Image.Image,
    subject_config: dict,
    page_number: int,
    model: str | None = None,
    max_retries: int = 3,
    log_dir: str | None = None,
) -> tuple[list[dict] | dict, str]:
    """
    단일 페이지 이미지를 Claude Vision으로 파싱합니다.

    Returns:
        (parsed_result, raw_response_text)
        - parsed_result: list[dict] 또는 dict (국어)
        - raw_response_text: Claude의 원본 응답 텍스트
    """
    if model is None:
        model = subject_config.get("모델_기본", MODEL_SONNET)

    system_prompt = _build_system_prompt(subject_config)
    b64_image = image_to_base64(image)

    # 이미지 로그 저장
    if log_dir:
        img_path = os.path.join(log_dir, "images", f"page_{page_number:03d}.png")
        os.makedirs(os.path.dirname(img_path), exist_ok=True)
        image.save(img_path, "PNG")

    raw_text = ""
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": b64_image,
                                },
                            },
                            {
                                "type": "text",
                                "text": f"Parse all problems on page {page_number}. Return JSON only.",
                            },
                        ],
                    }
                ],
            )

            raw_text = response.content[0].text

            # API 응답 로그 저장
            if log_dir:
                resp_path = os.path.join(log_dir, "api_responses", f"page_{page_number:03d}.json")
                os.makedirs(os.path.dirname(resp_path), exist_ok=True)
                log_data = {
                    "page": page_number,
                    "model": model,
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "raw_response": raw_text,
                }
                with open(resp_path, "w", encoding="utf-8") as f:
                    json.dump(log_data, f, ensure_ascii=False, indent=2)

            result = _extract_json_from_response(raw_text)
            _inject_page_number(result, page_number)
            return result, raw_text

        except anthropic.BadRequestError as e:
            if "credit balance is too low" in str(e):
                raise RuntimeError(
                    "[크레딧 부족] Anthropic API 크레딧이 소진되었습니다.\n"
                    "  → https://console.anthropic.com/settings/billing 에서 충전 후 재실행하세요."
                ) from e
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"    [재시도 {attempt+1}/{max_retries}] {e} — {wait}초 대기")
                time.sleep(wait)
            else:
                _save_error_log(log_dir, page_number, str(e), raw_text)
                print(f"    [ERROR] 페이지 {page_number} 파싱 실패: {e}")
                return [], raw_text

        except (ValueError, anthropic.APIError) as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"    [재시도 {attempt+1}/{max_retries}] {e} — {wait}초 대기")
                time.sleep(wait)
            else:
                _save_error_log(log_dir, page_number, str(e), raw_text)
                print(f"    [ERROR] 페이지 {page_number} 파싱 실패: {e}")
                return [], raw_text

    return [], raw_text


def parse_answer_key_page(
    image: Image.Image,
    page_number: int,
    max_retries: int = 3,
    log_dir: str | None = None,
) -> dict:
    """
    정답표 페이지를 파싱하여 {문제번호: 정답} 딕셔너리를 반환합니다.
    """
    system_prompt = load_prompt("answer_key_prompt.txt")
    b64_image = image_to_base64(image)

    if log_dir:
        img_path = os.path.join(log_dir, "images", f"answer_key_page_{page_number:03d}.png")
        os.makedirs(os.path.dirname(img_path), exist_ok=True)
        image.save(img_path, "PNG")

    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=MODEL_SONNET,
                max_tokens=1024,
                system=system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": b64_image,
                                },
                            },
                            {
                                "type": "text",
                                "text": "Extract all answers from this answer key page. Return JSON only.",
                            },
                        ],
                    }
                ],
            )

            raw_text = response.content[0].text

            if log_dir:
                resp_path = os.path.join(log_dir, "api_responses", f"answer_key_page_{page_number:03d}.json")
                os.makedirs(os.path.dirname(resp_path), exist_ok=True)
                with open(resp_path, "w", encoding="utf-8") as f:
                    json.dump({"page": page_number, "raw_response": raw_text}, f, ensure_ascii=False, indent=2)

            result = _extract_json_from_response(raw_text)
            if isinstance(result, dict) and "정답" in result:
                return result["정답"]
            return result

        except anthropic.BadRequestError as e:
            if "credit balance is too low" in str(e):
                raise RuntimeError(
                    "[크레딧 부족] Anthropic API 크레딧이 소진되었습니다.\n"
                    "  → https://console.anthropic.com/settings/billing 에서 충전 후 재실행하세요."
                ) from e
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                time.sleep(wait)
            else:
                print(f"    [ERROR] 정답표 페이지 {page_number} 파싱 실패: {e}")
                return {}
        except (ValueError, anthropic.APIError) as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                time.sleep(wait)
            else:
                print(f"    [ERROR] 정답표 페이지 {page_number} 파싱 실패: {e}")
                return {}

    return {}


def parse_full_pdf(
    images: list[Image.Image],
    subject_config: dict,
    verbose: bool = True,
    log_dir: str | None = None,
) -> list[dict]:
    """
    전체 PDF 이미지 목록을 파싱하여 문제 배열을 반환합니다.
    중복 제거 키: 수학은 (번호, 형태, 선택과목명), 그 외는 (번호,)
    """
    all_problems: list[dict] = []
    seen_keys: set[tuple] = set()
    subject_type = subject_config["유형"]

    # 파싱 요약 로그 (페이지별 토큰/문제수)
    summary_rows: list[dict] = []

    for page_idx, image in enumerate(images):
        page_num = page_idx + 1
        if verbose:
            print(f"  [파싱] 페이지 {page_num:02d}/{len(images)} ...", end=" ", flush=True)

        model = _select_model(subject_config, page_idx, all_problems)
        result, raw_text = parse_page(
            image, subject_config, page_num, model=model, log_dir=log_dir
        )

        problems = _normalize_result(result, subject_type, page_num)

        new_count = 0
        for prob in problems:
            key = _make_dedup_key(prob, subject_type)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_problems.append(prob)
            new_count += 1

        summary_rows.append({
            "page": page_num,
            "model": model,
            "extracted": new_count,
            "cumulative": len(all_problems),
        })

        if verbose:
            print(f"{new_count}개 문제 (누적 {len(all_problems)}개)")

        time.sleep(0.5)

    # 파싱 요약 로그 저장
    if log_dir:
        summary_path = os.path.join(log_dir, "parse_summary.json")
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary_rows, f, ensure_ascii=False, indent=2)

    return all_problems


def _make_dedup_key(prob: dict, subject_type: str) -> tuple:
    """
    중복 제거에 사용할 키를 반환합니다.
    수학: (번호, 형태, 선택과목명) — 홀수/짝수, 선택과목별로 분리
    기타: (번호,)
    """
    num = prob.get("번호")
    if subject_type == "수학":
        return (num, prob.get("형태", ""), prob.get("선택과목명", ""))
    return (num,)


def _select_model(subject_config: dict, _page_idx: int, current_problems: list[dict]) -> str:
    """현재까지 파싱된 문제 번호 기준으로 모델을 선택합니다."""
    high_start = subject_config.get("고난도_시작_문제번호")
    if high_start is None:
        return subject_config.get("모델_기본", MODEL_SONNET)

    if current_problems:
        max_num = max((p.get("번호", 0) for p in current_problems), default=0)
        if max_num >= high_start - 2:
            return subject_config.get("모델_고난도", MODEL_SONNET)

    return subject_config.get("모델_기본", MODEL_SONNET)


def _normalize_result(result: Any, subject_type: str, _page_num: int) -> list[dict]:
    """파싱 결과를 문제 리스트로 정규화합니다."""
    if isinstance(result, list):
        return result

    if isinstance(result, dict):
        if subject_type == "국어":
            return result.get("문제", [])
        if "문제" in result:
            return result["문제"]
        if "번호" in result:
            return [result]

    return []


def _inject_page_number(result: Any, page_num: int) -> None:
    """파싱 결과에 페이지 번호가 없으면 주입합니다."""
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict) and "페이지" not in item:
                item["페이지"] = page_num
    elif isinstance(result, dict):
        for item in result.get("문제", []):
            if isinstance(item, dict) and "페이지" not in item:
                item["페이지"] = page_num


def _save_error_log(log_dir: str | None, page_num: int, error: str, raw_text: str) -> None:
    """파싱 오류를 로그 파일로 저장합니다."""
    if not log_dir:
        return
    err_path = os.path.join(log_dir, "errors", f"page_{page_num:03d}_error.json")
    os.makedirs(os.path.dirname(err_path), exist_ok=True)
    with open(err_path, "w", encoding="utf-8") as f:
        json.dump({"page": page_num, "error": error, "raw_response": raw_text}, f, ensure_ascii=False, indent=2)
