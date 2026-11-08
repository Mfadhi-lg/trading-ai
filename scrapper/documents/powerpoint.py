"""PPTX slide-structure extraction and bounded legacy PPT text recovery."""

from __future__ import annotations

import io
import struct
from typing import Any, Iterator

import olefile
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from .downloader import PPT, PPTX
from .extraction import (
    DocumentElement,
    DocumentPage,
    ExtractedDocument,
    normalize_extracted_text,
    text_usability,
)
from .ocr import OcrEngine


def _bbox(shape: Any) -> tuple[float, float, float, float]:
    return (
        float(shape.left),
        float(shape.top),
        float(shape.left + shape.width),
        float(shape.top + shape.height),
    )


def _pptx_notes(slide: Any) -> tuple[bool, str | None]:
    try:
        if not slide.has_notes_slide:
            return False, None
        frame = slide.notes_slide.notes_text_frame
        text = normalize_extracted_text(frame.text if frame is not None else "")
        return bool(text), text or None
    except (AttributeError, KeyError, ValueError):
        return False, None


def _pptx_slide_elements(slide: Any) -> tuple[DocumentElement, ...]:
    elements: list[DocumentElement] = []
    for shape in slide.shapes:
        reference = {
            "shape_name": shape.name,
            "shape_type": str(shape.shape_type),
        }
        bbox = _bbox(shape)
        if getattr(shape, "has_table", False):
            rows = tuple(
                tuple(normalize_extracted_text(cell.text) for cell in row.cells)
                for row in shape.table.rows
            )
            elements.append(
                DocumentElement(
                    position=len(elements),
                    kind="table",
                    text_origin="native",
                    rows=rows,
                    reference=reference,
                    bbox=bbox,
                )
            )
        elif getattr(shape, "has_chart", False):
            chart = shape.chart
            try:
                reference["chart_type"] = str(chart.chart_type)
                reference["series_count"] = len(chart.series)
            except (ValueError, AttributeError, KeyError) as exc:
                # A plot unsupported by python-pptx must not discard the slide
                # or its other tables/text. Phase 5 can still read original
                # OOXML caches; visual interpretation remains unresolved.
                reference["chart_type"] = "unsupported"
                reference["series_count"] = None
                reference["chart_structure_error"] = type(exc).__name__
            elements.append(
                DocumentElement(
                    position=len(elements),
                    kind="chart",
                    reference=reference,
                    bbox=bbox,
                )
            )
        elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            image = shape.image
            reference.update(
                {
                    "content_type": image.content_type,
                    "filename": image.filename,
                    "sha1": image.sha1,
                    "byte_size": len(image.blob),
                }
            )
            elements.append(
                DocumentElement(
                    position=len(elements),
                    kind="image",
                    reference=reference,
                    bbox=bbox,
                )
            )
        elif getattr(shape, "has_text_frame", False):
            text = normalize_extracted_text(shape.text)
            if text:
                elements.append(
                    DocumentElement(
                        position=len(elements),
                        kind="text_block",
                        text_origin="native",
                        text_raw=text,
                        title_raw=shape.name,
                        reference=reference,
                        bbox=bbox,
                    )
                )
    return tuple(elements)


def _ocr_pptx_pictures(slide: Any, ocr_engine: OcrEngine) -> tuple[str, str | None]:
    texts: list[str] = []
    confidences: list[float] = []
    for shape in slide.shapes:
        if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
            continue
        with Image.open(io.BytesIO(shape.image.blob)) as image:
            text, confidence = ocr_engine.extract_text(image.convert("RGB"))
        if text:
            texts.append(text)
        if confidence is not None:
            try:
                confidences.append(float(confidence))
            except ValueError:
                pass
    confidence = None
    if confidences:
        confidence = f"{sum(confidences) / len(confidences):.4f}"
    return normalize_extracted_text("\n".join(texts)), confidence


def extract_pptx(content: bytes, *, ocr_engine: OcrEngine) -> ExtractedDocument:
    presentation = Presentation(io.BytesIO(content))
    pages: list[DocumentPage] = []
    for slide_number, slide in enumerate(presentation.slides, 1):
        elements = _pptx_slide_elements(slide)
        native_parts: list[str] = []
        for element in elements:
            if element.kind == "text_block" and element.text_raw:
                native_parts.append(element.text_raw)
            elif element.kind == "table" and element.rows:
                native_parts.extend(" | ".join(row) for row in element.rows)
        native_text = normalize_extracted_text("\n".join(native_parts))
        native_usable, unusable_reason = text_usability(native_text)
        ocr_text = ""
        confidence: str | None = None
        selected_origin = "native" if native_usable else "none"
        selected_text = native_text if native_usable else ""
        if not native_usable:
            # A picture-only slide is already a rendered source surface. OCR the
            # embedded pictures in z-order; do not fabricate a visual from XML.
            ocr_text, confidence = _ocr_pptx_pictures(slide, ocr_engine)
            ocr_usable, _ = text_usability(ocr_text)
            if ocr_usable:
                selected_origin = "ocr"
                selected_text = ocr_text
        title = None
        if slide.shapes.title is not None:
            title = normalize_extracted_text(slide.shapes.title.text) or None
        notes_available, notes_text = _pptx_notes(slide)
        pages.append(
            DocumentPage(
                number=slide_number,
                kind="slide",
                title_raw=title,
                native_text=native_text,
                ocr_text=ocr_text,
                selected_text=selected_text,
                selected_text_origin=selected_origin,
                native_text_usable=native_usable,
                native_unusable_reason=unusable_reason,
                extraction_confidence=confidence,
                speaker_notes_available=notes_available,
                speaker_notes_text=notes_text,
                layout={
                    "width_emu": int(presentation.slide_width),
                    "height_emu": int(presentation.slide_height),
                    "rendered_for_ocr": False,
                    "ocr_surface": "embedded_pictures" if not native_usable else None,
                },
                elements=elements,
            )
        )
    return ExtractedDocument(
        media_type=PPTX,
        pages=tuple(pages),
        metadata={
            "format": "pptx",
            "slide_width_emu": int(presentation.slide_width),
            "slide_height_emu": int(presentation.slide_height),
        },
    )


def _records(data: bytes) -> Iterator[tuple[int, bytes]]:
    position = 0
    while position + 8 <= len(data):
        options, record_type, length = struct.unpack_from("<HHI", data, position)
        start = position + 8
        end = start + length
        if end > len(data):
            break
        payload = data[start:end]
        yield record_type, payload
        if options & 0xF == 0xF:
            yield from _records(payload)
        position = end


def extract_legacy_ppt(content: bytes, *, ocr_engine: OcrEngine) -> ExtractedDocument:
    """Recover ordered native slide text from the documented binary record stream.

    Legacy PPT has no stable cross-platform renderer in this project. Image-only
    legacy slides therefore remain explicit unusable slides instead of receiving
    invented OCR output. Text-bearing PPT files retain real SlidePersist order.
    """

    del ocr_engine
    with olefile.OleFileIO(io.BytesIO(content)) as container:
        if not container.exists("PowerPoint Document"):
            raise ValueError("Legacy PPT is missing the PowerPoint Document stream")
        stream = container.openstream("PowerPoint Document").read()
        has_pictures = container.exists("Pictures")
    slides: list[list[str]] = []
    current: list[str] | None = None
    for record_type, payload in _records(stream):
        if record_type == 1011:  # SlidePersistAtom
            current = []
            slides.append(current)
        elif current is not None and record_type == 4000:  # TextCharsAtom
            current.append(payload.decode("utf-16le", errors="replace"))
        elif current is not None and record_type == 4008:  # TextBytesAtom
            current.append(payload.decode("cp1252", errors="replace"))
    pages: list[DocumentPage] = []
    for slide_number, parts in enumerate(slides, 1):
        native_text = normalize_extracted_text("\n".join(parts))
        native_usable, unusable_reason = text_usability(native_text)
        elements = tuple(
            DocumentElement(
                position=position,
                kind="text_block",
                text_origin="native",
                text_raw=normalize_extracted_text(part),
            )
            for position, part in enumerate(parts)
            if normalize_extracted_text(part)
        )
        if has_pictures and slide_number == 1:
            elements = elements + (
                DocumentElement(
                    position=len(elements),
                    kind="image",
                    reference={"container_stream": "Pictures", "slide_mapping": "unknown"},
                ),
            )
        pages.append(
            DocumentPage(
                number=slide_number,
                kind="slide",
                title_raw=elements[0].text_raw if elements and elements[0].kind == "text_block" else None,
                native_text=native_text,
                ocr_text="",
                selected_text=native_text if native_usable else "",
                selected_text_origin="native" if native_usable else "none",
                native_text_usable=native_usable,
                native_unusable_reason=unusable_reason,
                extraction_confidence=None,
                layout={"format": "binary_ppt", "rendered_for_ocr": False},
                elements=elements,
            )
        )
    return ExtractedDocument(
        media_type=PPT,
        pages=tuple(pages),
        metadata={"format": "binary_ppt", "pictures_stream_present": has_pictures},
    )


def extract_powerpoint(
    content: bytes, *, media_type: str, ocr_engine: OcrEngine
) -> ExtractedDocument:
    if media_type == PPTX:
        return extract_pptx(content, ocr_engine=ocr_engine)
    if media_type == PPT:
        return extract_legacy_ppt(content, ocr_engine=ocr_engine)
    raise ValueError(f"Unsupported PowerPoint media type: {media_type}")
