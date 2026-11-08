"""Byte signatures, declared types and logical artifact media allowlists."""
from __future__ import annotations
import io
import zipfile
from typing import Any, Mapping

PDF = "application/pdf"
PPT = "application/vnd.ms-powerpoint"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

PRESENTATION_MEDIA_TYPES = frozenset({PDF, PPT, PPTX})
AUDIO_VIDEO_MEDIA_TYPES = frozenset(
    {
        "audio/flac",
        "audio/mpeg",
        "audio/mp4",
        "audio/ogg",
        "audio/wav",
        "video/mp4",
        "video/mpeg",
        "video/quicktime",
        "video/webm",
        "video/x-msvideo",
    }
)

ALLOWED_MEDIA_BY_ARTIFACT = {
    "presentation": PRESENTATION_MEDIA_TYPES,
    "credit_rating": frozenset({PDF}),
    "transcript": frozenset({PDF}),
    "recording": AUDIO_VIDEO_MEDIA_TYPES,
}

GENERIC_DECLARED_MEDIA_TYPES = frozenset(
    {
        "",
        "application/download",
        "application/force-download",
        "application/octet-stream",
        "binary/octet-stream",
    }
)

DECLARED_MEDIA_ALIASES = {
    "application/x-pdf": PDF,
    "application/acrobat": PDF,
    "application/mspowerpoint": PPT,
    "application/powerpoint": PPT,
    "application/x-mspowerpoint": PPT,
    "audio/mp3": "audio/mpeg",
    "audio/x-mp3": "audio/mpeg",
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/x-flac": "audio/flac",
    "application/ogg": "audio/ogg",
    "application/mp4": "video/mp4",
    "video/x-m4v": "video/mp4",
}

STORED_RESPONSE_HEADERS = frozenset(
    {
        "accept-ranges",
        "content-disposition",
        "content-length",
        "content-range",
        "content-type",
        "etag",
        "last-modified",
        "location",
        "retry-after",
    }
)
REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


def _declared_media_type(headers: Mapping[str, Any]) -> str | None:
    value = next(
        (str(v) for k, v in headers.items() if str(k).lower() == "content-type"),
        None,
    )
    if value is None:
        return None
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type or None


def _safe_response_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(key).lower(): str(value)
        for key, value in headers.items()
        if str(key).lower() in STORED_RESPONSE_HEADERS
    }


def _looks_like_html(content: bytes) -> bool:
    sample = content[:4096].lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    return sample.startswith(
        (
            b"<!doctype html", b"<html", b"<head", b"<body", b"<?xml",
            b"<title", b"<meta", b"<link", b"<style", b"<div",
        )
    ) or b"<html" in sample[:1024]


def _is_pptx(content: bytes) -> bool:
    if not content.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
    except (OSError, ValueError, zipfile.BadZipFile):
        return False
    return "[Content_Types].xml" in names and "ppt/presentation.xml" in names


def _is_legacy_ppt(content: bytes) -> bool:
    if not content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return False
    # CFB is shared by old Word/Excel/PowerPoint files. Requiring PowerPoint's
    # directory stream names prevents a generic OLE file from being accepted.
    return (
        "PowerPoint Document".encode("utf-16le") in content
        and "Current User".encode("utf-16le") in content
    )


def detect_media_type(content: bytes, declared_media_type: str | None = None) -> str | None:
    """Return a canonical media type from the bytes, never the URL extension."""

    if not content or _looks_like_html(content):
        return "text/html" if content and _looks_like_html(content) else None
    if content.find(b"%PDF-", 0, 1024) >= 0:
        return PDF
    if _is_pptx(content):
        return PPTX
    if _is_legacy_ppt(content):
        return PPT
    if content.startswith(b"fLaC"):
        return "audio/flac"
    if content.startswith(b"OggS"):
        return "audio/ogg"
    if content.startswith(b"RIFF") and len(content) >= 12:
        if content[8:12] == b"WAVE":
            return "audio/wav"
        if content[8:12] == b"AVI ":
            return "video/x-msvideo"
    if content.startswith(b"ID3") or (
        len(content) >= 2 and content[0] == 0xFF and (content[1] & 0xE0) == 0xE0
    ):
        return "audio/mpeg"
    if content.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    if content.startswith((b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3")):
        return "video/mpeg"
    if len(content) >= 12 and content[4:8] == b"ftyp":
        brand = content[8:12]
        if brand in {b"M4A ", b"M4B ", b"F4A ", b"F4B "}:
            return "audio/mp4"
        if brand == b"qt  ":
            return "video/quicktime"
        if declared_media_type and declared_media_type.startswith("audio/"):
            return "audio/mp4"
        return "video/mp4"
    return None


def _declared_type_matches(declared: str | None, detected: str) -> bool:
    declared_key = declared or ""
    if declared_key in GENERIC_DECLARED_MEDIA_TYPES:
        return True
    canonical = DECLARED_MEDIA_ALIASES.get(declared_key, declared_key)
    if canonical == detected:
        return True
    # WebM and Ogg are sometimes served with the correct container but the
    # opposite audio/video top-level type. Both remain recording media.
    return {canonical, detected} in (
        {"audio/webm", "video/webm"},
        {"audio/ogg", "video/ogg"},
    )
