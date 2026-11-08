"""Offline verification for the dependencies and tools shipped in the runtime image."""

from __future__ import annotations

import argparse
import io
import json
import platform
import re
import subprocess
import tempfile
from importlib import metadata
from pathlib import Path
from typing import Any


_PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^ ;\\]+)(?:\s*;\s*([^\\]+))?")


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def selected_lock_versions(path: Path, *, evaluate_markers: bool = True) -> dict[str, str]:
    """Return pins applicable to this interpreter from a generated lock file."""

    Marker = None
    if evaluate_markers:
        from packaging.markers import Marker

    selected: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _PIN.match(line)
        if not match:
            continue
        name, version, marker = match.groups()
        if marker and evaluate_markers and not Marker(marker.strip()).evaluate():
            continue
        selected[_canonical_name(name)] = version
    return selected


def _package_check(lock_path: Path) -> dict[str, Any]:
    expected = selected_lock_versions(lock_path)
    installed = {
        _canonical_name(distribution.metadata["Name"]): distribution.version
        for distribution in metadata.distributions()
        if distribution.metadata.get("Name")
    }
    missing = sorted(name for name in expected if name not in installed)
    mismatched = {
        name: {"expected": expected[name], "actual": installed[name]}
        for name in sorted(expected)
        if name in installed and installed[name] != expected[name]
    }
    if missing or mismatched:
        raise RuntimeError(
            f"dependency lock mismatch: missing={missing}, mismatched={mismatched}"
        )
    subprocess.run(
        ["python", "-m", "pip", "check"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return {"locked_package_count": len(expected), "missing": [], "mismatched": {}}


def _browser_check() -> dict[str, str]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
        if not executable.is_file():
            raise RuntimeError(f"Playwright Chromium is missing: {executable}")
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content("<title>runtime-capability</title><main>browser-ok</main>")
            if page.title() != "runtime-capability" or page.locator("main").inner_text() != "browser-ok":
                raise RuntimeError("Playwright Chromium smoke page did not round-trip")
            version = browser.version
        finally:
            browser.close()
    return {"engine": "chromium", "executable": str(executable), "version": version}


def _ocr_check(work: Path) -> dict[str, str]:
    from PIL import Image, ImageDraw, ImageFont

    from scrapper.documents.ocr import TesseractOcr

    image = Image.new("RGB", (800, 180), "white")
    draw = ImageDraw.Draw(image)
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    font = ImageFont.truetype(str(font_path), 72) if font_path.is_file() else None
    draw.text((20, 35), "TRADING 2026", fill="black", font=font)
    fixture = work / "ocr.png"
    image.save(fixture)
    engine = TesseractOcr()
    text, confidence = engine.extract_text(image)
    if "TRADING" not in text.upper() or not any(character.isdigit() for character in text):
        raise RuntimeError(f"Tesseract smoke text mismatch: {text!r}")
    return {"engine": engine.name, "version": engine.version, "confidence": confidence or ""}


def _tui_check() -> dict[str, str]:
    import prompt_toolkit
    import rich
    from rich.console import Console
    from rich.table import Table

    console = Console(file=io.StringIO(), record=True, force_terminal=False, width=40)
    table = Table("capability")
    table.add_row("tui-ok")
    console.print(table)
    if "tui-ok" not in console.export_text():
        raise RuntimeError("Rich render smoke output is missing")
    return {
        "prompt_toolkit": prompt_toolkit.__version__,
        "rich": metadata.version("rich"),
        "rich_module": str(Path(rich.__file__).resolve()),
    }


def _media_check(work: Path) -> dict[str, Any]:
    from scrapper.documents.media import FfmpegMediaTool

    source = work / "source.wav"
    normalized = work / "normalized.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "sine=frequency=440:duration=0.25", "-ac", "2", "-ar", "44100", str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    tool = FfmpegMediaTool(timeout_seconds=30, max_output_bytes=8 * 1024 * 1024)
    source_probe = tool.probe(source)
    tool.normalize_audio(source, normalized)
    normalized_probe = tool.probe(normalized)
    audio = normalized_probe.audio_streams
    if len(audio) != 1 or audio[0].sample_rate_hz != 16000 or audio[0].channel_count != 1:
        raise RuntimeError(f"FFmpeg normalization smoke mismatch: {normalized_probe.to_dict()}")
    return {
        "ffmpeg": tool.version,
        "ffprobe": tool.probe_version,
        "source_duration_ms": source_probe.duration_ms,
        "normalized_duration_ms": normalized_probe.duration_ms,
    }


def verify_runtime(*, lock_path: Path, expected_python: str) -> dict[str, Any]:
    actual_python = platform.python_version()
    if actual_python != expected_python:
        raise RuntimeError(
            f"Python version mismatch: expected {expected_python}, found {actual_python}"
        )
    with tempfile.TemporaryDirectory(prefix="trading-ai-capabilities-") as temporary:
        work = Path(temporary)
        return {
            "status": "ok",
            "python": actual_python,
            "packages": _package_check(lock_path),
            "browser": _browser_check(),
            "ocr": _ocr_check(work),
            "tui": _tui_check(),
            "media": _media_check(work),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("/app/requirements.lock"))
    parser.add_argument("--expected-python", default="3.12.14")
    args = parser.parse_args(argv)
    print(json.dumps(verify_runtime(lock_path=args.lock, expected_python=args.expected_python), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
