"""
PDF → PNG 변환 모듈
pdf2image(poppler)를 사용하여 페이지별 이미지를 생성합니다.
"""
import os
from pathlib import Path
from typing import Generator

from pdf2image import convert_from_path
from PIL import Image

from config import PDF_DPI

# Homebrew(Apple Silicon)의 poppler 경로
POPPLER_PATH = "/opt/homebrew/bin"


def pdf_to_images(pdf_path: str, dpi: int = PDF_DPI) -> list[Image.Image]:
    """PDF 파일을 페이지별 PIL Image 리스트로 변환합니다."""
    pdf_path = str(pdf_path)
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    images = convert_from_path(
        pdf_path,
        dpi=dpi,
        fmt="png",
        thread_count=4,
        poppler_path=POPPLER_PATH,
    )
    print(f"  [PDF→PNG] {Path(pdf_path).name}: {len(images)} 페이지")
    return images


def pdf_to_images_chunked(
    pdf_path: str,
    chunk_size: int = 2,
    dpi: int = PDF_DPI,
) -> Generator[tuple[int, list[Image.Image]], None, None]:
    """
    PDF를 chunk_size 페이지씩 묶어서 yield 합니다.
    메모리 효율을 위해 대용량 PDF에 사용합니다.

    Yields:
        (start_page_1indexed, [Image, ...])
    """
    images = pdf_to_images(pdf_path, dpi=dpi)
    for i in range(0, len(images), chunk_size):
        yield i + 1, images[i : i + chunk_size]


def image_to_base64(image: Image.Image) -> str:
    """PIL Image를 base64 인코딩된 PNG 문자열로 변환합니다."""
    import base64
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return base64.standard_b64encode(buffer.read()).decode("utf-8")


def save_images(images: list[Image.Image], output_dir: str, prefix: str = "page") -> list[str]:
    """변환된 이미지를 파일로 저장하고 경로 목록을 반환합니다. (디버깅용)"""
    os.makedirs(output_dir, exist_ok=True)
    paths = []
    for i, img in enumerate(images, start=1):
        path = os.path.join(output_dir, f"{prefix}_{i:03d}.png")
        img.save(path, "PNG")
        paths.append(path)
    return paths
