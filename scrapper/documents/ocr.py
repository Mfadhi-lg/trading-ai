"""Recorded OCR fallback used only when native page/slide text is unusable."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import pytesseract

from .extraction import normalize_extracted_text


class OcrEngine(Protocol):
    name: str
    version: str
    language: str

    def extract_text(self, image: Any) -> tuple[str, str | None]: ...


@dataclass(frozen=True)
class TesseractOcr:
    language: str = "eng"
    config: str = "--psm 6"
    name: str = "tesseract"

    @property
    def version(self) -> str:
        return str(pytesseract.get_tesseract_version()).splitlines()[0]

    def extract_text(self, image: Any) -> tuple[str, str | None]:
        data = pytesseract.image_to_data(
            image,
            lang=self.language,
            config=self.config,
            output_type=pytesseract.Output.DICT,
        )
        tokens: list[str] = []
        confidences: list[float] = []
        for text, confidence in zip(data.get("text", ()), data.get("conf", ())):
            cleaned = str(text).strip()
            if not cleaned:
                continue
            tokens.append(cleaned)
            try:
                numeric_confidence = float(confidence)
            except (TypeError, ValueError):
                continue
            if numeric_confidence >= 0:
                confidences.append(numeric_confidence)
        result = normalize_extracted_text(" ".join(tokens))
        mean = None
        if confidences:
            mean = f"{sum(confidences) / len(confidences) / 100:.4f}"
        return result, mean

    def options(self) -> dict[str, str]:
        return {"language": self.language, "config": self.config}
