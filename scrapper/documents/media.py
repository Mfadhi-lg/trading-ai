"""Deterministic D06 media probing and recording-to-audio normalization."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Protocol


NORMALIZED_AUDIO_MEDIA_TYPE = "audio/wav"
NORMALIZED_AUDIO_OPTIONS = {
    "audio_stream": "first",
    "channel_count": 1,
    "codec": "pcm_s16le",
    "container": "wav",
    "metadata": "removed",
    "sample_format": "s16",
    "sample_rate_hz": 16000,
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _milliseconds(value: Any) -> int | None:
    if value in (None, "", "N/A"):
        return None
    try:
        seconds = Decimal(str(value))
    except InvalidOperation:
        return None
    if not seconds.is_finite() or seconds < 0:
        return None
    return int((seconds * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _optional_int(value: Any) -> int | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class MediaStream:
    index: int
    codec_type: str
    codec_name: str
    duration_ms: int | None
    sample_rate_hz: int | None = None
    channel_count: int | None = None
    channel_layout: str | None = None
    width: int | None = None
    height: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "codec_type": self.codec_type,
            "codec_name": self.codec_name,
            "duration_ms": self.duration_ms,
            "sample_rate_hz": self.sample_rate_hz,
            "channel_count": self.channel_count,
            "channel_layout": self.channel_layout,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class MediaProbe:
    container_formats: tuple[str, ...]
    duration_ms: int | None
    streams: tuple[MediaStream, ...]

    @property
    def audio_streams(self) -> tuple[MediaStream, ...]:
        return tuple(row for row in self.streams if row.codec_type == "audio")

    @property
    def video_streams(self) -> tuple[MediaStream, ...]:
        return tuple(row for row in self.streams if row.codec_type == "video")

    def to_dict(self) -> dict[str, Any]:
        return {
            "container_formats": list(self.container_formats),
            "duration_ms": self.duration_ms,
            "streams": [row.to_dict() for row in self.streams],
        }


@dataclass(frozen=True)
class MediaValidation:
    rule_code: str
    passed: bool
    expected_value: str
    actual_value: str
    details: dict[str, Any]
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_code": self.rule_code,
            "severity": self.severity,
            "status": "passed" if self.passed else "failed",
            "expected_value": self.expected_value,
            "actual_value": self.actual_value,
            "details": self.details,
        }


class MediaTool(Protocol):
    name: str
    version: str
    probe_name: str
    probe_version: str

    def options(self) -> dict[str, Any]: ...

    def probe(self, path: Path) -> MediaProbe: ...

    def normalize_audio(self, source: Path, destination: Path) -> None: ...


def validate_normalized_audio(
    source: MediaProbe,
    normalized: MediaProbe,
    *,
    normalized_body: bytes,
    source_sha256: str,
    normalized_sha256: str,
) -> tuple[MediaValidation, ...]:
    """Apply the complete D06 target contract without repairing probe output."""

    audio = normalized.audio_streams
    duration_delta = (
        abs(source.duration_ms - normalized.duration_ms)
        if source.duration_ms is not None and normalized.duration_ms is not None
        else None
    )
    rows = (
        MediaValidation(
            "recording_source_audio_stream_present",
            bool(source.audio_streams),
            ">=1",
            str(len(source.audio_streams)),
            {"source_stream_count": len(source.streams)},
        ),
        MediaValidation(
            "recording_source_duration_known",
            source.duration_ms is not None and source.duration_ms > 0,
            ">0ms",
            str(source.duration_ms),
            {},
        ),
        MediaValidation(
            "normalized_audio_wav_signature",
            len(normalized_body) >= 12
            and normalized_body[:4] == b"RIFF"
            and normalized_body[8:12] == b"WAVE",
            "RIFF....WAVE",
            normalized_body[:12].hex(),
            {},
        ),
        MediaValidation(
            "normalized_audio_container",
            "wav" in normalized.container_formats,
            "wav",
            ",".join(normalized.container_formats),
            {},
        ),
        MediaValidation(
            "normalized_audio_single_stream",
            len(normalized.streams) == 1 and len(audio) == 1,
            "1 audio stream and no other streams",
            f"{len(audio)} audio/{len(normalized.streams)} total",
            {},
        ),
        MediaValidation(
            "normalized_audio_codec",
            len(audio) == 1 and audio[0].codec_name == "pcm_s16le",
            "pcm_s16le",
            audio[0].codec_name if len(audio) == 1 else "unavailable",
            {},
        ),
        MediaValidation(
            "normalized_audio_sample_rate",
            len(audio) == 1 and audio[0].sample_rate_hz == 16000,
            "16000",
            str(audio[0].sample_rate_hz) if len(audio) == 1 else "unavailable",
            {"unit": "Hz"},
        ),
        MediaValidation(
            "normalized_audio_channel_count",
            len(audio) == 1 and audio[0].channel_count == 1,
            "1",
            str(audio[0].channel_count) if len(audio) == 1 else "unavailable",
            {},
        ),
        MediaValidation(
            "normalized_audio_duration_matches_source",
            duration_delta is not None and duration_delta <= 50,
            "absolute delta <=50ms",
            str(duration_delta),
            {
                "source_duration_ms": source.duration_ms,
                "normalized_duration_ms": normalized.duration_ms,
            },
        ),
        MediaValidation(
            "normalized_audio_is_separate_content",
            source_sha256 != normalized_sha256,
            "different SHA-256 from original media",
            "different" if source_sha256 != normalized_sha256 else "identical",
            {},
        ),
    )
    return rows


def _tool_version(executable: str) -> str:
    try:
        result = subprocess.run(
            [executable, "-version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Required media tool is unavailable: {executable}") from exc
    first_line = result.stdout.splitlines()[0] if result.stdout else ""
    parts = first_line.split()
    if len(parts) < 3 or parts[1] != "version":
        raise RuntimeError(f"Could not identify media tool version: {executable}")
    return parts[2]


class FfmpegMediaTool:
    """Use FFmpeg with a fixed PCM/WAV target suitable for later transcription."""

    name = "ffmpeg"
    probe_name = "ffprobe"

    def __init__(
        self,
        *,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        timeout_seconds: float = 300.0,
        max_output_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        if max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.version = _tool_version(ffmpeg)
        self.probe_version = _tool_version(ffprobe)

    def options(self) -> dict[str, Any]:
        return {
            **NORMALIZED_AUDIO_OPTIONS,
            "bitexact": True,
            "max_output_bytes": self.max_output_bytes,
            "timeout_seconds": self.timeout_seconds,
        }

    def probe(self, path: Path) -> MediaProbe:
        command = [
            self.ffprobe,
            "-v",
            "error",
            "-show_entries",
            (
                "format=format_name,duration:"
                "stream=index,codec_type,codec_name,duration,sample_rate,channels,"
                "channel_layout,width,height"
            ),
            "-of",
            "json",
            str(path),
        ]
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=min(self.timeout_seconds, 60.0),
            )
        except OSError as exc:
            raise RuntimeError("ffprobe could not be executed") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("ffprobe timed out") from exc
        except subprocess.CalledProcessError as exc:
            diagnostic = (exc.stderr or "").strip()[-1000:]
            raise ValueError(f"ffprobe rejected media: {diagnostic}") from exc
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError("ffprobe returned invalid JSON") from exc
        streams: list[MediaStream] = []
        for position, row in enumerate(payload.get("streams", [])):
            codec_type = str(row.get("codec_type") or "unknown")
            streams.append(
                MediaStream(
                    index=(
                        _optional_int(row.get("index"))
                        if _optional_int(row.get("index")) is not None
                        else position
                    ),
                    codec_type=codec_type,
                    codec_name=str(row.get("codec_name") or "unknown"),
                    duration_ms=_milliseconds(row.get("duration")),
                    sample_rate_hz=_optional_int(row.get("sample_rate")),
                    channel_count=_optional_int(row.get("channels")),
                    channel_layout=(str(row["channel_layout"]) if row.get("channel_layout") else None),
                    width=_optional_int(row.get("width")),
                    height=_optional_int(row.get("height")),
                )
            )
        format_row = payload.get("format") or {}
        duration_ms = _milliseconds(format_row.get("duration"))
        if duration_ms is None:
            durations = [row.duration_ms for row in streams if row.duration_ms is not None]
            duration_ms = max(durations) if durations else None
        format_names = tuple(
            item.strip()
            for item in str(format_row.get("format_name") or "").split(",")
            if item.strip()
        )
        return MediaProbe(
            container_formats=format_names,
            duration_ms=duration_ms,
            streams=tuple(streams),
        )

    def normalize_audio(self, source: Path, destination: Path) -> None:
        command = [
            self.ffmpeg,
            "-y",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-map_metadata",
            "-1",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-sample_fmt",
            "s16",
            "-c:a",
            "pcm_s16le",
            "-fflags",
            "+bitexact",
            "-flags:a",
            "+bitexact",
            "-fs",
            str(self.max_output_bytes),
            str(destination),
        ]
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        except OSError as exc:
            raise RuntimeError("ffmpeg could not be executed") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("ffmpeg audio normalization timed out") from exc
        except subprocess.CalledProcessError as exc:
            diagnostic = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
            raise ValueError(f"ffmpeg could not normalize recording: {diagnostic[-1000:]}") from exc
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("ffmpeg did not produce normalized audio")
