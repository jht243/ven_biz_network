"""
OCR engine using Tesseract with the Spanish language pack.

Pipeline:
  1. PDF → page images (via PyMuPDF)
  2. Image preprocessing (OpenCV: deskew, binarize, denoise)
  3. OCR extraction (Tesseract) with per-page confidence scores
  4. Concatenated text output
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import fitz  # PyMuPDF
import numpy as np
import pytesseract
from PIL import Image

from src.config import settings

logger = logging.getLogger(__name__)

pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd

DPI = 300
CONFIDENCE_THRESHOLD = 40


@dataclass
class OCRResult:
    text: str
    avg_confidence: int
    page_count: int
    low_confidence_pages: list[int]


def ocr_pdf(pdf_path: str | Path) -> OCRResult:
    """Run OCR on an entire PDF, returning combined text and confidence."""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(str(pdf_path))
    all_text: list[str] = []
    all_confidences: list[float] = []
    low_conf_pages: list[int] = []

    logger.info("Starting OCR on %s (%d pages)", pdf_path.name, len(doc))

    for page_num in range(len(doc)):
        page = doc[page_num]

        pix = page.get_pixmap(dpi=DPI)
        img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )

        if pix.n == 4:  # RGBA → RGB
            img_array = cv2.cvtColor(img_array, cv2.COLOR_RGBA2RGB)

        processed = _preprocess_image(img_array)

        page_text, page_conf = _ocr_image(processed)
        all_text.append(page_text)
        all_confidences.append(page_conf)

        if page_conf < CONFIDENCE_THRESHOLD:
            low_conf_pages.append(page_num + 1)

        logger.debug(
            "Page %d/%d: confidence=%d%%, chars=%d",
            page_num + 1, len(doc), page_conf, len(page_text),
        )

    doc.close()

    avg_confidence = int(sum(all_confidences) / len(all_confidences)) if all_confidences else 0
    combined_text = "\n\n--- PAGE BREAK ---\n\n".join(all_text)

    if low_conf_pages:
        logger.warning(
            "Low OCR confidence on pages %s of %s (avg=%d%%)",
            low_conf_pages, pdf_path.name, avg_confidence,
        )

    result = OCRResult(
        text=combined_text,
        avg_confidence=avg_confidence,
        page_count=len(all_text),
        low_confidence_pages=low_conf_pages,
    )

    _save_ocr_output(pdf_path, result)
    return result


def _preprocess_image(img: np.ndarray) -> np.ndarray:
    """
    Prepare a scanned page image for OCR:
    - Convert to grayscale
    - Adaptive threshold for binarization (handles uneven lighting)
    - Denoise to remove speckle from poor scans
    - Deskew to straighten rotated pages
    """
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

    denoised = cv2.fastNlMeansDenoising(gray, h=10)

    binary = cv2.adaptiveThreshold(
        denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )

    deskewed = _deskew(binary)
    return deskewed


def _deskew(image: np.ndarray) -> np.ndarray:
    """Correct slight rotation in scanned documents."""
    coords = np.column_stack(np.where(image < 128))
    if len(coords) < 100:
        return image

    angle = cv2.minAreaRect(coords)[-1]

    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    # Only correct small rotations (< 10 degrees)
    if abs(angle) > 10 or abs(angle) < 0.1:
        return image

    h, w = image.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        image, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return rotated


def _ocr_image(image: np.ndarray) -> tuple[str, float]:
    """
    Run Tesseract on a preprocessed image.
    Returns (extracted_text, avg_confidence_0_to_100).
    """
    pil_img = Image.fromarray(image)

    custom_config = (
        f"--oem 3 --psm 6 -l {settings.tesseract_lang}"
    )

    # Get detailed data for confidence scoring
    data = pytesseract.image_to_data(
        pil_img, config=custom_config, output_type=pytesseract.Output.DICT
    )

    confidences = [
        int(c) for c, t in zip(data["conf"], data["text"])
        if int(c) > 0 and t.strip()
    ]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0

    text = pytesseract.image_to_string(pil_img, config=custom_config)

    return text.strip(), avg_conf


def _save_ocr_output(pdf_path: Path, result: OCRResult) -> None:
    """Save raw OCR text alongside the PDF for audit purposes."""
    out_dir = settings.storage_dir / "ocr_output"
    out_file = out_dir / f"{pdf_path.stem}.txt"
    out_file.write_text(result.text, encoding="utf-8")
    logger.info(
        "Saved OCR output: %s (confidence=%d%%, pages=%d)",
        out_file, result.avg_confidence, result.page_count,
    )
