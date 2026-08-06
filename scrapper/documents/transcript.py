"""Deterministic official-transcript segmentation from persisted D03 pages."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


OFFICIAL_TRANSCRIPT_PARSER_NAME = "official-transcript-segmenter"
OFFICIAL_TRANSCRIPT_PARSER_VERSION = "1.0.0"
OFFICIAL_TRANSCRIPT_OPTIONS = {
    "boundary_rule": "explicit_heading_or_moderator_qa_phrase_v1",
    "continuation_rule": "until_next_speaker_or_end_marker_v1",
    "speaker_rule": "explicit_colon_label_v1",
}

_PREPARED_HEADINGS = {
    "presentation",
    "prepared remarks",
    "opening remarks",
    "prepared remarks section",
}
_QA_HEADINGS = {
    "q&a",
    "qa",
    "question and answer session",
    "question-and-answer session",
    "questions and answers",
    "question and answer",
}
_END_HEADINGS = {
    "end of transcript",
    "end of call",
    "conference call concluded",
}
_QA_PHRASE = re.compile(r"\b(?:question[- ]and[- ]answer|q\s*&\s*a)\s+session\b", re.I)
_SPEAKER = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z0-9 .,'’&()\-/]{0,100}?)\s*:\s*(?P<body>.+)$"
)
_RESERVED_LABELS = {
    "date",
    "time",
    "page",
    "company",
    "participants",
    "management participants",
    "analyst participants",
    "disclaimer",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _compact(value: str | None) -> str:
    return re.sub(r"[ \t]+", " ", value or "").strip()


def _heading_key(value: str) -> str:
    compact = _compact(value).lower().strip(" :-–—")
    return re.sub(r"\s+", " ", compact)


@dataclass(frozen=True)
class TranscriptFragment:
    page_number: int
    element_position: int | None
    line_position: int | None
    source_kind: str
    text_origin: str
    text_raw: str
    body_raw: str
    bbox: list[float] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "element_position": self.element_position,
            "line_position": self.line_position,
            "source_kind": self.source_kind,
            "text_origin": self.text_origin,
            "text_raw": self.text_raw,
            "body_raw": self.body_raw,
            "bbox": self.bbox,
        }


@dataclass(frozen=True)
class TranscriptBoundary:
    position: int
    section_kind: str
    page_number: int
    element_position: int | None
    label_raw: str
    text_origin: str
    bbox: list[float] | None


@dataclass(frozen=True)
class OfficialTranscriptSegment:
    position: int
    speaker_raw: str
    speaker_normalized: str
    text_raw: str
    section_kind: str
    page_start: int
    page_end: int
    element_start: int | None
    element_end: int | None
    text_origin: str
    fragments: tuple[TranscriptFragment, ...]


@dataclass(frozen=True)
class OfficialTranscript:
    document_marker_raw: str | None
    document_marker_page: int | None
    segments: tuple[OfficialTranscriptSegment, ...]
    boundaries: tuple[TranscriptBoundary, ...]
    validations: tuple[dict[str, Any], ...]

    @property
    def status(self) -> str:
        if any(
            row["severity"] == "error" and not row["passed"]
            for row in self.validations
        ):
            return "failed"
        if any(
            row["severity"] == "manual_review" and not row["passed"]
            for row in self.validations
        ):
            return "manual_review"
        return "accepted"


@dataclass(frozen=True)
class _Line:
    page_number: int
    element_position: int | None
    line_position: int | None
    text_origin: str
    source_kind: str
    text_raw: str
    bbox: list[float] | None


def _page_lines(page: dict[str, Any]) -> tuple[_Line, ...]:
    page_number = int(page["page_or_slide_number"])
    blocks = [
        element
        for element in page["elements"]
        if element["element_kind"] == "text_block" and _compact(element.get("text_raw"))
    ]
    if blocks:
        return tuple(
            _Line(
                page_number=page_number,
                element_position=int(element["element_position"]),
                line_position=None,
                text_origin=element["text_origin"],
                source_kind="text_block",
                text_raw=_compact(element["text_raw"]),
                bbox=element.get("bbox"),
            )
            for element in blocks
        )
    # OCR-selected PDF pages do not currently have synthetic D03 text-block
    # elements. Retain exact page/line provenance rather than inventing a bbox.
    return tuple(
        _Line(
            page_number=page_number,
            element_position=None,
            line_position=line_position,
            text_origin=page["selected_text_origin"],
            source_kind="selected_page_text",
            text_raw=_compact(line),
            bbox=None,
        )
        for line_position, line in enumerate(page["selected_text"].splitlines())
        if _compact(line)
    )


def _section_heading(value: str) -> str | None:
    key = _heading_key(value)
    if key in _PREPARED_HEADINGS:
        return "prepared_remarks"
    if key in _QA_HEADINGS:
        return "qa"
    return None


def _speaker(value: str) -> tuple[str, str] | None:
    match = _SPEAKER.match(_compact(value))
    if not match:
        return None
    label = _compact(match.group("label"))
    body = _compact(match.group("body"))
    if label.lower() in _RESERVED_LABELS or not body:
        return None
    if label.lower() not in {"moderator", "operator"}:
        connective = {"and", "of", "the", "for"}
        words = re.findall(r"[A-Za-z][A-Za-z'’]*", label)
        if not words or any(
            word.lower() not in connective and not word[0].isupper()
            for word in words
        ):
            return None
    # A transcript label is evidence, not an invitation to infer an identity.
    return label, body


def _origin(fragments: list[TranscriptFragment]) -> str:
    origins = {fragment.text_origin for fragment in fragments}
    return next(iter(origins)) if len(origins) == 1 else "mixed"


def _validations(
    marker: str | None,
    segments: list[OfficialTranscriptSegment],
    boundaries: list[TranscriptBoundary],
) -> tuple[dict[str, Any], ...]:
    positions = [segment.position for segment in segments]
    page_ranges_valid = all(
        segment.page_start >= 1
        and segment.page_end >= segment.page_start
        and all(
            segment.page_start <= fragment.page_number <= segment.page_end
            for fragment in segment.fragments
        )
        for segment in segments
    )
    fragments_complete = all(
        segment.fragments
        and "\n".join(fragment.body_raw for fragment in segment.fragments)
        == segment.text_raw
        for segment in segments
    )
    boundary_sections = [boundary.section_kind for boundary in boundaries]
    boundary_order_valid = (
        boundary_sections == sorted(
            boundary_sections,
            key=lambda item: {"prepared_remarks": 0, "qa": 1}[item],
        )
        and len(boundary_sections) == len(set(boundary_sections))
    )
    unknown = [segment.position for segment in segments if segment.section_kind == "unknown"]
    return (
        {
            "rule_code": "official_transcript_document_marker",
            "severity": "error",
            "passed": marker is not None,
            "expected": "explicit transcript marker",
            "actual": marker,
            "details": {},
        },
        {
            "rule_code": "official_transcript_segments_present",
            "severity": "error",
            "passed": bool(segments),
            "expected": ">=1",
            "actual": len(segments),
            "details": {},
        },
        {
            "rule_code": "official_transcript_segment_order_contiguous",
            "severity": "error",
            "passed": positions == list(range(len(segments))),
            "expected": list(range(len(segments))),
            "actual": positions,
            "details": {},
        },
        {
            "rule_code": "official_transcript_speaker_labels_present",
            "severity": "error",
            "passed": all(segment.speaker_raw and segment.speaker_normalized for segment in segments),
            "expected": True,
            "actual": all(segment.speaker_raw and segment.speaker_normalized for segment in segments),
            "details": {},
        },
        {
            "rule_code": "official_transcript_page_ranges_valid",
            "severity": "error",
            "passed": page_ranges_valid,
            "expected": True,
            "actual": page_ranges_valid,
            "details": {},
        },
        {
            "rule_code": "official_transcript_fragment_evidence_complete",
            "severity": "error",
            "passed": fragments_complete,
            "expected": True,
            "actual": fragments_complete,
            "details": {},
        },
        {
            "rule_code": "official_transcript_boundaries_ordered",
            "severity": "error",
            "passed": boundary_order_valid,
            "expected": "prepared_remarks then qa, without duplicates",
            "actual": boundary_sections,
            "details": {},
        },
        {
            "rule_code": "official_transcript_section_boundaries_explicit",
            "severity": "manual_review",
            "passed": bool(boundaries) and not unknown,
            "expected": "explicit boundary before every segment",
            "actual": {"boundary_count": len(boundaries), "unknown_segments": unknown},
            "details": {},
        },
    )


def parse_official_transcript(extraction: dict[str, Any]) -> OfficialTranscript:
    """Split an official D03 extraction only where labels/boundaries are explicit."""

    if extraction.get("extraction_status") != "ok":
        raise ValueError("Official transcript ingestion requires an ok extraction")
    pages = list(extraction.get("pages") or ())
    if not pages or any(page.get("page_kind") != "page" for page in pages):
        raise ValueError("Official transcript ingestion requires ordered PDF pages")

    marker_raw: str | None = None
    marker_page: int | None = None
    for page in pages:
        for line in page["selected_text"].splitlines():
            if "transcript" in line.lower():
                marker_raw = _compact(line)
                marker_page = int(page["page_or_slide_number"])
                break
        if marker_raw:
            break

    boundaries: list[TranscriptBoundary] = []
    segments: list[OfficialTranscriptSegment] = []
    section = "unknown"
    current_speaker: str | None = None
    current_fragments: list[TranscriptFragment] = []
    stopped = False

    def flush() -> None:
        nonlocal current_speaker, current_fragments
        if current_speaker is None or not current_fragments:
            current_speaker = None
            current_fragments = []
            return
        segments.append(
            OfficialTranscriptSegment(
                position=len(segments),
                speaker_raw=current_speaker,
                speaker_normalized=_compact(current_speaker),
                text_raw="\n".join(fragment.body_raw for fragment in current_fragments),
                section_kind=section,
                page_start=current_fragments[0].page_number,
                page_end=current_fragments[-1].page_number,
                element_start=current_fragments[0].element_position,
                element_end=current_fragments[-1].element_position,
                text_origin=_origin(current_fragments),
                fragments=tuple(current_fragments),
            )
        )
        current_speaker = None
        current_fragments = []

    def set_section(next_section: str, line: _Line) -> None:
        nonlocal section
        if next_section == section:
            return
        flush()
        section = next_section
        boundaries.append(
            TranscriptBoundary(
                position=len(boundaries),
                section_kind=next_section,
                page_number=line.page_number,
                element_position=line.element_position,
                label_raw=line.text_raw,
                text_origin=line.text_origin,
                bbox=line.bbox,
            )
        )

    for page in pages:
        if stopped:
            break
        for line in _page_lines(page):
            key = _heading_key(line.text_raw)
            if key in _END_HEADINGS:
                flush()
                stopped = True
                break
            heading_section = _section_heading(line.text_raw)
            if heading_section:
                set_section(heading_section, line)
                continue
            speaker = _speaker(line.text_raw)
            if speaker:
                label, body = speaker
                if _QA_PHRASE.search(body):
                    set_section("qa", line)
                else:
                    flush()
                current_speaker = label
                current_fragments = [
                    TranscriptFragment(
                        page_number=line.page_number,
                        element_position=line.element_position,
                        line_position=line.line_position,
                        source_kind=line.source_kind,
                        text_origin=line.text_origin,
                        text_raw=line.text_raw,
                        body_raw=body,
                        bbox=line.bbox,
                    )
                ]
                continue
            if current_speaker is not None:
                current_fragments.append(
                    TranscriptFragment(
                        page_number=line.page_number,
                        element_position=line.element_position,
                        line_position=line.line_position,
                        source_kind=line.source_kind,
                        text_origin=line.text_origin,
                        text_raw=line.text_raw,
                        body_raw=line.text_raw,
                        bbox=line.bbox,
                    )
                )
    flush()
    validations = _validations(marker_raw, segments, boundaries)
    return OfficialTranscript(
        document_marker_raw=marker_raw,
        document_marker_page=marker_page,
        segments=tuple(segments),
        boundaries=tuple(boundaries),
        validations=validations,
    )
