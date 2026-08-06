"""Deterministic native PDF layout extraction with page-local OCR fallback."""

from __future__ import annotations

import io
from collections import defaultdict
from typing import Any

import pdfplumber

from .downloader import PDF
from .extraction import (
    DocumentElement,
    DocumentPage,
    ExtractedDocument,
    normalize_extracted_text,
    text_usability,
)
from .ocr import OcrEngine


PDF_RENDER_DPI = 200


def _rounded_bbox(values: tuple[Any, Any, Any, Any]) -> tuple[float, float, float, float]:
    return tuple(round(float(value), 3) for value in values)  # type: ignore[return-value]


def _text_blocks(page: Any) -> list[DocumentElement]:
    words = page.extract_words(
        x_tolerance=2,
        y_tolerance=3,
        keep_blank_chars=False,
        use_text_flow=True,
    )
    lines: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for word in words:
        lines[round(float(word["top"]), 1)].append(word)
    blocks: list[DocumentElement] = []
    for top in sorted(lines):
        row = sorted(lines[top], key=lambda item: (float(item["x0"]), float(item["x1"])))
        text = " ".join(str(item["text"]) for item in row).strip()
        if not text:
            continue
        blocks.append(
            DocumentElement(
                position=0,
                kind="text_block",
                text_origin="native",
                text_raw=text,
                bbox=_rounded_bbox(
                    (
                        min(item["x0"] for item in row),
                        min(item["top"] for item in row),
                        max(item["x1"] for item in row),
                        max(item["bottom"] for item in row),
                    )
                ),
            )
        )
    return blocks


def _table_elements(page: Any) -> list[DocumentElement]:
    elements: list[DocumentElement] = []
    for table_index, table in enumerate(page.find_tables()):
        rows = tuple(
            tuple(normalize_extracted_text(cell) for cell in row)
            for row in table.extract()
        )
        elements.append(
            DocumentElement(
                position=0,
                kind="table",
                text_origin="native",
                rows=rows,
                reference={"table_index": table_index},
                bbox=_rounded_bbox(tuple(table.bbox)),
            )
        )
    return elements


def _image_elements(page: Any) -> list[DocumentElement]:
    elements: list[DocumentElement] = []
    for image_index, image in enumerate(page.images):
        elements.append(
            DocumentElement(
                position=0,
                kind="image",
                reference={
                    "image_index": image_index,
                    "name": image.get("name"),
                    "width": round(float(image.get("width", 0)), 3),
                    "height": round(float(image.get("height", 0)), 3),
                    "bits": image.get("bits"),
                    "colorspace": (
                        [str(value) for value in image.get("colorspace", ())]
                        if isinstance(image.get("colorspace"), (list, tuple))
                        else str(image.get("colorspace"))
                        if image.get("colorspace") is not None
                        else None
                    ),
                },
                bbox=_rounded_bbox(
                    (image["x0"], image["top"], image["x1"], image["bottom"])
                ),
            )
        )
    return elements


def _ordered_elements(page: Any) -> tuple[DocumentElement, ...]:
    elements = _text_blocks(page) + _table_elements(page) + _image_elements(page)
    kind_order = {"text_block": 0, "table": 1, "image": 2, "chart": 3}
    elements.sort(
        key=lambda item: (
            item.bbox[1] if item.bbox else float("inf"),
            item.bbox[0] if item.bbox else float("inf"),
            kind_order[item.kind],
        )
    )
    return tuple(
        DocumentElement(
            position=position,
            kind=item.kind,
            text_origin=item.text_origin,
            text_raw=item.text_raw,
            title_raw=item.title_raw,
            rows=item.rows,
            reference=item.reference,
            bbox=item.bbox,
        )
        for position, item in enumerate(elements)
    )


def extract_pdf(content: bytes, *, ocr_engine: OcrEngine) -> ExtractedDocument:
    pages: list[DocumentPage] = []
    with pdfplumber.open(io.BytesIO(content)) as document:
        for page_number, page in enumerate(document.pages, 1):
            native_text = normalize_extracted_text(
                page.extract_text(x_tolerance=2, y_tolerance=3, layout=False)
            )
            native_usable, unusable_reason = text_usability(native_text)
            ocr_text = ""
            confidence: str | None = None
            selected_origin = "native" if native_usable else "none"
            selected_text = native_text if native_usable else ""
            if not native_usable:
                rendered = page.to_image(
                    resolution=PDF_RENDER_DPI,
                    antialias=False,
                    force_mediabox=True,
                ).original
                ocr_text, confidence = ocr_engine.extract_text(rendered)
                ocr_usable, _ = text_usability(ocr_text)
                if ocr_usable:
                    selected_origin = "ocr"
                    selected_text = ocr_text
            elements = _ordered_elements(page)
            title = next(
                (
                    element.text_raw
                    for element in elements
                    if element.kind == "text_block" and element.text_raw
                ),
                None,
            )
            pages.append(
                DocumentPage(
                    number=page_number,
                    kind="page",
                    title_raw=title,
                    native_text=native_text,
                    ocr_text=ocr_text,
                    selected_text=selected_text,
                    selected_text_origin=selected_origin,
                    native_text_usable=native_usable,
                    native_unusable_reason=unusable_reason,
                    extraction_confidence=confidence,
                    layout={
                        "width_points": round(float(page.width), 3),
                        "height_points": round(float(page.height), 3),
                        "rotation_degrees": int(page.rotation or 0),
                        "rendered_for_ocr": not native_usable,
                        "render_dpi": PDF_RENDER_DPI if not native_usable else None,
                    },
                    elements=elements,
                )
            )
        metadata = {
            "format": "pdf",
            "metadata": {
                str(key): str(value) if value is not None else None
                for key, value in (document.metadata or {}).items()
            },
        }
    return ExtractedDocument(media_type=PDF, pages=tuple(pages), metadata=metadata)
