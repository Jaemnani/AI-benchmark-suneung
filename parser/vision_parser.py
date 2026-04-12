"""
Claude Vision API를 사용하여 시험지 페이지를 파싱합니다.
"""
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import anthropic
from PIL import Image

from config import ANTHROPIC_API_KEY, MODEL_SONNET, PROMPT_DIR
from pdf_converter import image_to_base64

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# 국어는 페이지 단위로 지문+문제를 합쳐서 반환하므로 별도 병합 처리
KOREAN_SUBJECT = "국어"


def load_prompt(prompt_file: str) -> str:
    """prompts/ 디렉토리에서 프롬프트 파일을 읽어 반환합니다."""
    path = os.path.join(PROMPT_DIR, prompt_file)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _build_system_prompt(subject_config: dict) -> str:
    """과목 설정을 바탕으로 system 프롬프트를 구성합니다."""
    base = load_prompt(subject_config["프롬프트"])
    subject_type = subject_config["유형"]

    # 과목명 컨텍스트 추가
    extras = []
    if "과목명" in subject_config:
        extras.append(f"Subject: {subject_config['과목명']}")
    if "언어" in subject_config:
        extras.append(f"Language: {subject_config['언어']}")
    if subject_config.get("방향") == "RTL":
        extras.append("Text direction: RTL (Right-to-Left). Add \"방향\": \"RTL\" to each problem.")

    if extras:
        base = "\n".join(extras) + "\n\n" + base

    return base


def _extract_json_from_response(text: str) -> Any:
    """Claude 응답에서 JSON 부분만 추출합니다."""
    text = text.strip()

    # 마크다운 코드 블록 제거
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```\s*$", "", text)
    text = text.strip()

    # JSON 파싱 시도
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 배열 또는 객체 시작점 찾기
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
) -> list[dict] | dict:
    """
    단일 페이지 이미지를 Claude Vision으로 파싱합니다.

    Returns:
        - 대부분 영역: list[dict] (문제 배열)
        - 국어: dict (지문+문제 혼합 구조)
    """
    if model is None:
        model = subject_config.get("모델_기본", MODEL_SONNET)

    system_prompt = _build_system_prompt(subject_config)
    b64_image = image_to_base64(image)

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
            result = _extract_json_from_response(raw_text)

            # 페이지 번호 보정 (누락된 경우)
            _inject_page_number(result, page_number)

            return result

        except (ValueError, anthropic.APIError) as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"    [재시도 {attempt+1}/{max_retries}] {e} — {wait}초 대기")
                time.sleep(wait)
            else:
                print(f"    [ERROR] 페이지 {page_number} 파싱 실패: {e}")
                return []


def parse_answer_key_page(
    image: Image.Image,
    page_number: int,
    max_retries: int = 3,
) -> dict:
    """
    정답표 페이지를 파싱하여 {문제번호: 정답} 딕셔너리를 반환합니다.
    """
    system_prompt = load_prompt("answer_key_prompt.txt")
    b64_image = image_to_base64(image)

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
            result = _extract_json_from_response(raw_text)

            # {"정답": {"1": "②", ...}} 또는 {"1": "②", ...} 두 형태 모두 처리
            if isinstance(result, dict) and "정답" in result:
                return result["정답"]
            return result

        except (ValueError, anthropic.APIError) as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                time.sleep(wait)
            else:
                print(f"    [ERROR] 정답표 페이지 {page_number} 파싱 실패: {e}")
                return {}


def parse_full_pdf(
    images: list[Image.Image],
    subject_config: dict,
    verbose: bool = True,
) -> list[dict]:
    """
    전체 PDF 이미지 목록을 파싱하여 문제 배열을 반환합니다.
    국어의 경우 지문-문제 구조를 flatten합니다.
    """
    all_problems: list[dict] = []
    seen_numbers: set[int] = set()

    subject_type = subject_config["유형"]

    for page_idx, image in enumerate(images):
        page_num = page_idx + 1
        if verbose:
            print(f"  [파싱] 페이지 {page_num}/{len(images)} ...", end=" ")

        # 고난도 페이지 → Opus 모델 사용 (수학 전용)
        model = _select_model(subject_config, page_idx, all_problems)
        result = parse_page(image, subject_config, page_num, model=model)

        problems = _normalize_result(result, subject_type, page_num)

        # 중복 제거
        new_count = 0
        for prob in problems:
            num = prob.get("번호")
            if num is not None and num in seen_numbers:
                continue
            if num is not None:
                seen_numbers.add(num)
            all_problems.append(prob)
            new_count += 1

        if verbose:
            print(f"{new_count}개 문제 추출 (누적 {len(all_problems)}개)")

        # API 속도 제한 방지
        time.sleep(0.5)

    return all_problems


def _select_model(subject_config: dict, page_idx: int, current_problems: list[dict]) -> str:
    """현재까지 파싱된 문제 수를 기준으로 모델을 선택합니다."""
    high_start = subject_config.get("고난도_시작_문제번호")
    if high_start is None:
        return subject_config.get("모델_기본", MODEL_SONNET)

    # 이미 파싱된 문제 중 최대 번호 확인
    if current_problems:
        max_num = max((p.get("번호", 0) for p in current_problems), default=0)
        if max_num >= high_start - 2:  # 고난도 근접 시 Opus 전환
            return subject_config.get("모델_고난도", MODEL_SONNET)

    return subject_config.get("모델_기본", MODEL_SONNET)


def _normalize_result(result: Any, subject_type: str, page_num: int) -> list[dict]:
    """파싱 결과를 문제 리스트로 정규화합니다."""
    if isinstance(result, list):
        return result

    if isinstance(result, dict):
        # 국어: {"페이지_유형": ..., "지문": [...], "문제": [...]}
        if subject_type == "국어":
            return result.get("문제", [])

        # {"문제": [...]} 형태
        if "문제" in result:
            return result["문제"]

        # 단일 문제 객체
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
        if "문제" in result:
            for item in result.get("문제", []):
                if isinstance(item, dict) and "페이지" not in item:
                    item["페이지"] = page_num
