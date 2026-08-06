"""Source-faithful document extraction records shared by PDF and PowerPoint."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


DOCUMENT_PARSER_NAME = "document-structure"
DOCUMENT_PARSER_VERSION = "1.0.0"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def normalize_extracted_text(value: str | None) -> str:
    if not value:
        return ""
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def text_usability(value: str | None) -> tuple[bool, str | None]:
    """Apply a deliberately small, deterministic native/OCR usability rule."""

    text = normalize_extracted_text(value)
    if not text:
        return False, "absent"
    alphanumeric = sum(character.isalnum() for character in text)
    if alphanumeric < 3:
        return False, "fewer_than_3_alphanumeric_characters"
    replacement = text.count("\ufffd")
    if replacement and replacement / max(len(text), 1) > 0.05:
        return False, "replacement_character_ratio_above_0_05"
    return True, None


@dataclass(frozen=True)
class DocumentElement:
    position: int
    kind: str
    text_origin: str = "none"
    text_raw: str | None = None
    title_raw: str | None = None
    rows: tuple[tuple[str, ...], ...] | None = None
    reference: dict[str, Any] | None = None
    bbox: tuple[float, float, float, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_position": self.position,
            "element_kind": self.kind,
            "text_origin": self.text_origin,
            "text_raw": self.text_raw,
            "title_raw": self.title_raw,
            "rows": [list(row) for row in self.rows] if self.rows is not None else None,
            "reference": self.reference,
            "bbox": list(self.bbox) if self.bbox is not None else None,
        }


@dataclass(frozen=True)
class DocumentPage:
    number: int
    kind: str
    native_text: str
    ocr_text: str
    selected_text: str
    selected_text_origin: str
    native_text_usable: bool
    native_unusable_reason: str | None
    extraction_confidence: str | None
    title_raw: str | None = None
    speaker_notes_available: bool = False
    speaker_notes_text: str | None = None
    layout: dict[str, Any] = field(default_factory=dict)
    elements: tuple[DocumentElement, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_or_slide_number": self.number,
            "page_kind": self.kind,
            "title_raw": self.title_raw,
            "native_text": self.native_text,
            "ocr_text": self.ocr_text,
            "selected_text": self.selected_text,
            "selected_text_origin": self.selected_text_origin,
            "native_text_usable": self.native_text_usable,
            "native_unusable_reason": self.native_unusable_reason,
            "extraction_confidence": self.extraction_confidence,
            "speaker_notes_available": self.speaker_notes_available,
            "speaker_notes_text": self.speaker_notes_text,
            "layout": self.layout,
            "elements": [element.to_dict() for element in self.elements],
        }


@dataclass(frozen=True)
class ExtractedDocument:
    media_type: str
    pages: tuple[DocumentPage, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if not self.pages or all(page.selected_text_origin == "none" for page in self.pages):
            return "failed"
        if any(page.selected_text_origin == "none" for page in self.pages):
            return "partial"
        return "ok"

    @property
    def native_page_count(self) -> int:
        return sum(page.selected_text_origin == "native" for page in self.pages)

    @property
    def ocr_page_count(self) -> int:
        return sum(page.selected_text_origin == "ocr" for page in self.pages)

    @property
    def unusable_page_count(self) -> int:
        return sum(page.selected_text_origin == "none" for page in self.pages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "media_type": self.media_type,
            "extraction_status": self.status,
            "page_or_slide_count": len(self.pages),
            "native_page_count": self.native_page_count,
            "ocr_page_count": self.ocr_page_count,
            "unusable_page_count": self.unusable_page_count,
            "metadata": self.metadata,
            "pages": [page.to_dict() for page in self.pages],
        }
