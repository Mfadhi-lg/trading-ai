"""Shared deterministic JSON serialization for existing reporting adapters.

Callers retain payload construction, path selection and directory creation order.
"""
from __future__ import annotations
import json
from pathlib import Path


def write_json_payload(payload: object, output: Path, *, ensure_ascii: bool = True) -> Path:
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=ensure_ascii) + "\n",
        encoding="utf-8",
    )
    return output
