"""D07 machine-transcription models, offline adapter and validations."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Protocol


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _decimal_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not decimal.is_finite():
        return None
    return format(decimal, "f")


def _milliseconds(seconds: Any) -> int:
    value = Decimal(str(seconds))
    if not value.is_finite() or value < 0:
        raise ValueError(f"Invalid transcription timestamp: {seconds!r}")
    return int((value * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class EngineSegment:
    start_ms: int
    end_ms: int
    text_raw: str
    confidence: str | None
    uncertainty: dict[str, Any]

    def to_dict(self, position: int) -> dict[str, Any]:
        return {
            "segment_position": position,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "speaker_raw": "unknown",
            "speaker_normalized": "unknown",
            "text_raw": self.text_raw,
            "confidence": self.confidence,
            "uncertainty": self.uncertainty,
            "section_kind": "unknown",
            "text_origin": "machine_generated",
        }


@dataclass(frozen=True)
class EngineTranscript:
    language: str
    language_confidence: str | None
    segments: tuple[EngineSegment, ...]


class Transcriber(Protocol):
    engine: str
    engine_version: str
    model_version: str

    def options(self) -> dict[str, Any]: ...

    def transcribe(self, path: Path) -> EngineTranscript: ...


@dataclass(frozen=True)
class TranscriptionValidation:
    rule_code: str
    passed: bool
    expected: Any
    actual: Any
    details: dict[str, Any]
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_code": self.rule_code,
            "severity": self.severity,
            "status": "passed" if self.passed else "failed",
            "expected": self.expected,
            "actual": self.actual,
            "details": self.details,
        }


@dataclass(frozen=True)
class ValidatedMachineTranscript:
    language: str
    language_confidence: str | None
    segments: tuple[EngineSegment, ...]
    duration_coverage: str
    validations: tuple[TranscriptionValidation, ...]
    status: str


def validate_machine_transcript(
    result: EngineTranscript, *, source_duration_ms: int
) -> ValidatedMachineTranscript:
    """Validate without repairing, reordering or inventing engine output."""

    segments = result.segments
    nonempty = all(bool(row.text_raw.strip()) for row in segments)
    valid_ranges = all(
        row.start_ms >= 0 and row.end_ms > row.start_ms for row in segments
    )
    monotonic = all(
        current.start_ms >= previous.end_ms
        for previous, current in zip(segments, segments[1:])
    )
    within_duration = bool(segments) and all(
        row.end_ms <= source_duration_ms + 50 for row in segments
    )
    confidence_values: list[Decimal] = []
    confidence_valid = True
    for row in segments:
        if row.confidence is None:
            continue
        try:
            value = Decimal(row.confidence)
        except InvalidOperation:
            confidence_valid = False
            continue
        if not value.is_finite() or value < 0 or value > 1:
            confidence_valid = False
        else:
            confidence_values.append(value)
    spoken_ms = sum(
        max(0, min(row.end_ms, source_duration_ms) - max(row.start_ms, 0))
        for row in segments
    )
    coverage = (
        Decimal(spoken_ms) / Decimal(source_duration_ms)
        if source_duration_ms > 0
        else Decimal(0)
    )
    coverage_text = format(coverage.quantize(Decimal("0.000001")), "f")
    edge_tolerance = max(5000, source_duration_ms // 100)
    edge_coverage = bool(segments) and (
        segments[0].start_ms <= edge_tolerance
        and source_duration_ms - segments[-1].end_ms <= edge_tolerance
    )
    validations = (
        TranscriptionValidation(
            "machine_transcript_segments_present",
            bool(segments),
            ">=1",
            len(segments),
            {},
        ),
        TranscriptionValidation(
            "machine_transcript_text_nonempty",
            nonempty,
            "all segment text non-empty",
            sum(bool(row.text_raw.strip()) for row in segments),
            {"segment_count": len(segments)},
        ),
        TranscriptionValidation(
            "machine_transcript_timestamp_ranges_valid",
            valid_ranges,
            "0 <= start_ms < end_ms",
            [[row.start_ms, row.end_ms] for row in segments],
            {},
        ),
        TranscriptionValidation(
            "machine_transcript_timestamps_monotonic",
            monotonic,
            "non-overlapping source order",
            [[row.start_ms, row.end_ms] for row in segments],
            {},
        ),
        TranscriptionValidation(
            "machine_transcript_within_media_duration",
            within_duration,
            f"end_ms <= {source_duration_ms + 50}",
            max((row.end_ms for row in segments), default=None),
            {"source_duration_ms": source_duration_ms, "tolerance_ms": 50},
        ),
        TranscriptionValidation(
            "machine_transcript_language_detected",
            bool(result.language.strip()) and result.language != "und",
            "detected language code other than und",
            result.language,
            {"language_confidence": result.language_confidence},
        ),
        TranscriptionValidation(
            "machine_transcript_confidence_valid",
            confidence_valid,
            "confidence absent or decimal in [0,1]",
            [row.confidence for row in segments],
            {"reported_confidence_count": len(confidence_values)},
        ),
        TranscriptionValidation(
            "machine_transcript_neutral_speakers",
            True,
            "unknown",
            "unknown",
            {"speaker_identity_inference": False},
        ),
        TranscriptionValidation(
            "machine_transcript_duration_coverage_recorded",
            Decimal(0) <= coverage <= Decimal("1.000001"),
            "decimal ratio in [0,1]",
            coverage_text,
            {"spoken_ms": spoken_ms, "source_duration_ms": source_duration_ms},
        ),
        TranscriptionValidation(
            "machine_transcript_edge_coverage",
            edge_coverage,
            f"leading/trailing gap <= {edge_tolerance}ms",
            (
                {
                    "leading_gap_ms": segments[0].start_ms,
                    "trailing_gap_ms": source_duration_ms - segments[-1].end_ms,
                }
                if segments
                else None
            ),
            {"source_duration_ms": source_duration_ms},
            severity="manual_review",
        ),
    )
    if any(not row.passed and row.severity == "error" for row in validations):
        status = "failed"
    elif any(
        not row.passed and row.severity == "manual_review" for row in validations
    ):
        status = "manual_review"
    else:
        status = "accepted"
    return ValidatedMachineTranscript(
        language=result.language.strip() or "und",
        language_confidence=result.language_confidence,
        segments=segments,
        duration_coverage=coverage_text,
        validations=validations,
        status=status,
    )


def _file_sha256(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.digest()


def _model_inventory_sha256(path: Path) -> str:
    files = [path] if path.is_file() else sorted(row for row in path.rglob("*") if row.is_file())
    if not files:
        raise ValueError(f"Local transcription model has no files: {path}")
    digest = hashlib.sha256()
    for file_path in files:
        relative = file_path.name if path.is_file() else file_path.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_file_sha256(file_path))
    return digest.hexdigest()


class FasterWhisperTranscriber:
    """Lazy, local-files-only faster-whisper adapter."""

    engine = "faster-whisper"

    def __init__(
        self,
        model_path: Path | None,
        *,
        device: str = "cpu",
        compute_type: str = "int8",
        language: str | None = None,
        beam_size: int = 5,
    ) -> None:
        self.model_path = model_path.resolve() if model_path is not None else None
        self._engine_version: str | None = None
        self._model_version: str | None = None
        self._options = {
            "beam_size": beam_size,
            "compute_type": compute_type,
            "condition_on_previous_text": True,
            "device": device,
            "language": language,
            "local_files_only": True,
            "temperature": 0,
            "vad_filter": False,
            "word_timestamps": False,
        }

    def _ensure_identity(self) -> None:
        if self._engine_version is not None and self._model_version is not None:
            return
        if self.model_path is None:
            raise ValueError("--model-path is required when transcription is needed")
        if not self.model_path.exists():
            raise ValueError(
                f"Local transcription model does not exist: {self.model_path}"
            )
        try:
            self._engine_version = importlib.metadata.version("faster-whisper")
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeError(
                "faster-whisper is not installed; install it in the scraper runtime"
            ) from exc
        model_hash = _model_inventory_sha256(self.model_path)
        self._model_version = f"{self.model_path.name}@sha256:{model_hash}"

    @property
    def engine_version(self) -> str:
        self._ensure_identity()
        assert self._engine_version is not None
        return self._engine_version

    @property
    def model_version(self) -> str:
        self._ensure_identity()
        assert self._model_version is not None
        return self._model_version

    def options(self) -> dict[str, Any]:
        return dict(self._options)

    def transcribe(self, path: Path) -> EngineTranscript:
        self._ensure_identity()
        assert self.model_path is not None
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("faster-whisper could not be imported") from exc
        model = WhisperModel(
            str(self.model_path),
            device=self._options["device"],
            compute_type=self._options["compute_type"],
            local_files_only=True,
        )
        segments, info = model.transcribe(
            str(path),
            beam_size=self._options["beam_size"],
            language=self._options["language"],
            condition_on_previous_text=True,
            temperature=0,
            vad_filter=False,
            word_timestamps=False,
        )
        rows: list[EngineSegment] = []
        for segment in segments:
            average_log_probability = _decimal_text(segment.avg_logprob)
            confidence = None
            if average_log_probability is not None:
                confidence = format(
                    Decimal(str(math.exp(float(average_log_probability)))).quantize(
                        Decimal("0.000001")
                    ),
                    "f",
                )
            rows.append(
                EngineSegment(
                    start_ms=_milliseconds(segment.start),
                    end_ms=_milliseconds(segment.end),
                    text_raw=str(segment.text),
                    confidence=confidence,
                    uncertainty={
                        "average_log_probability": average_log_probability,
                        "compression_ratio": _decimal_text(segment.compression_ratio),
                        "no_speech_probability": _decimal_text(
                            segment.no_speech_prob
                        ),
                    },
                )
            )
        return EngineTranscript(
            language=str(info.language or "und"),
            language_confidence=_decimal_text(info.language_probability),
            segments=tuple(rows),
        )
