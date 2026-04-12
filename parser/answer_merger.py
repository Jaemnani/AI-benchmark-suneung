"""
정답표 PDF를 파싱하여 문제 배열에 정답을 병합합니다.
영어 듣기는 MP3 파일명도 매핑합니다.
"""
import os
from pathlib import Path
from typing import Any

from pdf_converter import pdf_to_images
from vision_parser import parse_answer_key_page


# 영어 듣기 MP3 파일명 매핑 (번호 → 파일명)
ENGLISH_MP3_MAP = {
    1:  "01_문제 01.mp3",
    2:  "02_문제 02.mp3",
    3:  "03_문제 03.mp3",
    4:  "04_문제 04.mp3",
    5:  "05_문제 05.mp3",
    6:  "06_문제 06.mp3",
    7:  "07_문제_07.mp3",
    8:  "08_문제_08.mp3",
    9:  "09_문제 09.mp3",
    10: "10_문제_10.mp3",
    11: "11_문제 11.mp3",
    12: "12_문제 12.mp3",
    13: "13_문제 13.mp3",
    14: "14_문제_14.mp3",
    15: "15_문제 15.mp3",
    16: "16_문제 16~17.mp3",
    17: "16_문제 16~17.mp3",
}


def parse_answer_key(pdf_path: str) -> dict[str, Any]:
    """
    정답표 PDF를 파싱하여 {문제번호(str): 정답} 딕셔너리를 반환합니다.
    """
    print(f"  [정답표] {Path(pdf_path).name} 파싱 중...")
    images = pdf_to_images(pdf_path)
    combined: dict[str, Any] = {}

    for i, image in enumerate(images, start=1):
        page_answers = parse_answer_key_page(image, page_number=i)
        combined.update(page_answers)

    print(f"  [정답표] {len(combined)}개 정답 추출")
    return combined


def merge_answers(
    problems: list[dict],
    answers: dict[str, Any],
    subject_config: dict,
) -> list[dict]:
    """
    문제 배열에 정답을 병합합니다.
    영어의 경우 듣기 문제에 MP3 파일명도 주입합니다.
    """
    subject_type = subject_config.get("유형", "")
    mp3_dir = subject_config.get("듣기_mp3_dir", "")

    for problem in problems:
        num = problem.get("번호")
        if num is None:
            continue

        # 정답 주입
        answer = answers.get(str(num))
        if answer is not None:
            problem["정답"] = answer

        # 영어 듣기: MP3 파일명 주입
        if subject_type == "영어" and problem.get("유형") == "듣기":
            mp3_filename = ENGLISH_MP3_MAP.get(num)
            if mp3_filename:
                problem["mp3_파일"] = mp3_filename
                if mp3_dir:
                    problem["mp3_경로"] = os.path.join(mp3_dir, mp3_filename)

    return problems


def merge_listening_script(
    problems: list[dict],
    script_pdf_path: str,
) -> list[dict]:
    """
    영어 듣기 대본 PDF를 파싱하여 듣기 문제에 대본을 주입합니다.
    대본 PDF는 일반 텍스트로 구성되어 있어 Vision API 없이 처리 가능.
    (현재는 Vision API 방식 사용)
    """
    if not os.path.exists(script_pdf_path):
        print(f"  [경고] 듣기 대본 파일 없음: {script_pdf_path}")
        return problems

    from vision_parser import parse_page, load_prompt
    from pdf_converter import pdf_to_images
    from config import MODEL_SONNET

    print(f"  [듣기대본] {Path(script_pdf_path).name} 파싱 중...")
    images = pdf_to_images(script_pdf_path)

    # 임시 script config
    script_config = {
        "유형": "영어대본",
        "프롬프트": "english_prompt.txt",
        "모델_기본": MODEL_SONNET,
        "모델_고난도": MODEL_SONNET,
    }

    # 대본 텍스트를 번호별로 수집
    scripts: dict[int, str] = {}
    for i, image in enumerate(images, start=1):
        # 대본 전용 간단 파싱 (Vision API)
        from config import ANTHROPIC_API_KEY
        import anthropic
        from pdf_converter import image_to_base64

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        b64 = image_to_base64(image)

        response = client.messages.create(
            model=MODEL_SONNET,
            max_tokens=2048,
            system=(
                "Extract listening scripts from this Korean CSAT English listening transcript page. "
                "Return ONLY a JSON object: {\"1\": \"script text\", \"2\": \"script text\", ...} "
                "where keys are problem numbers and values are the full script text. "
                "Preserve English and Korean text exactly."
            ),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": b64},
                        },
                        {"type": "text", "text": "Extract all listening scripts from this page."},
                    ],
                }
            ],
        )

        import json, re
        text = response.content[0].text.strip()
        text = re.sub(r"```(?:json)?\s*", "", text)
        text = re.sub(r"```\s*$", "", text).strip()
        try:
            page_scripts = json.loads(text)
            for k, v in page_scripts.items():
                try:
                    scripts[int(k)] = v
                except ValueError:
                    pass
        except json.JSONDecodeError:
            print(f"    [경고] 대본 페이지 {i} 파싱 실패")

    # 문제에 대본 주입
    for problem in problems:
        num = problem.get("번호")
        if problem.get("유형") == "듣기" and num in scripts:
            problem["대본"] = scripts[num]

    print(f"  [듣기대본] {len(scripts)}개 대본 추출")
    return problems
