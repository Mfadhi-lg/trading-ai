"""Fail-closed identity checks for runtime write targets. Importing is inert."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

IDENTITY_MARKER = ".trading_ai_runtime_identity.json"
PRODUCTION_ROOTS = tuple(Path(value) for value in (
    "/Users/mohamedfadhi/Fadhi/trading-ai",
    "/Volumes/My Passport/passport/trading-ai",
    "/data",
    "/documents",
))
_logged: set[str] = set()


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Invalid runtime identity file: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid runtime identity file: {path}")
    return value


def _marker_ancestor(path: Path) -> Path | None:
    for parent in (path, *path.parents):
        marker = parent / IDENTITY_MARKER
        if marker.is_file():
            return marker
    return None


def _mount_identity(path: Path) -> str | None:
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    matches: list[tuple[int, str]] = []
    for line in lines:
        try:
            left, right = line.split(" - ", 1)
            fields = left.split()
            mountpoint = Path(fields[4].replace("\\040", " "))
            if _inside(path, mountpoint):
                matches.append((len(str(mountpoint)), fields[3] + " " + right))
        except (IndexError, ValueError):
            continue
    return max(matches)[1] if matches else None


def _public_id(value: object) -> str:
    text = str(value or "")
    return text if re.fullmatch(r"[A-Za-z0-9._:@/-]{1,128}", text) else "unavailable"


def assert_writable_targets(*, db: Path, store: Path, documents: Path,
                            extra: tuple[Path, ...] = ()) -> None:
    """Authorize resolved targets before directories, DBs, or workers are opened."""
    environment = os.environ.get("TRADING_AI_ENV", "").strip().lower()
    if environment not in {"production", "development", "test"}:
        raise RuntimeError(
            "TRADING_AI_ENV must be production, development, or test before writing"
        )
    core = {"db": Path(db).resolve(), "store": Path(store).resolve(),
            "documents": Path(documents).resolve()}
    extras = tuple(Path(path).resolve() for path in extra)
    identity_file = os.environ.get("TRADING_AI_RUNTIME_IDENTITY_FILE")
    identity = _read_json(Path(identity_file)) if identity_file else None
    clone = None

    if environment == "production":
        expected = {key: str(path) for key, path in core.items()}
        if (not identity or identity.get("environment") != "production" or
                identity.get("authorized") is not True or identity.get("targets") != expected):
            raise RuntimeError("Production writes require an authorized exact runtime identity")
        if not all(identity.get(key) for key in ("release_id", "git_commit", "image_id")):
            raise RuntimeError("Production release, Git commit and image identity are required")
        extra_roots = tuple(Path(value).resolve() for value in
                            identity.get("additional_writable_roots", ()))
        for path in extras:
            if not any(_inside(path, root) for root in
                       (core["store"], core["documents"], *extra_roots)):
                raise RuntimeError(f"Production extra writable target is not authorized: {path}")
        for role in ("store", "documents"):
            marker = core[role] / IDENTITY_MARKER
            value = _read_json(marker)
            roles = value.get("roles", [value.get("role")])
            if ((value.get("environment"), value.get("path"), value.get("release_id")) !=
                    ("production", str(core[role]), identity.get("target_release_ids", {}).get(role, identity["release_id"])) or role not in roles):
                raise RuntimeError(f"Production sentinel mismatch: {marker}")
    else:
        if identity and identity.get("environment") != environment:
            raise RuntimeError("Runtime identity environment mismatch")
        clone_file = os.environ.get("TRADING_AI_CLONE_IDENTITY_FILE")
        clone = _read_json(Path(clone_file)) if clone_file else None
        clone_root = None
        if clone:
            try:
                clone_root = Path(clone["root"])
            except (KeyError, TypeError):
                raise RuntimeError("Invalid isolated clone identity") from None
            if (clone.get("environment") != environment or not clone.get("clone_id") or
                    not clone_root.is_absolute() or clone_root.resolve() != clone_root):
                raise RuntimeError("Invalid isolated clone identity")
        for role, path in (*core.items(), *((f"extra_{i}", p) for i, p in enumerate(extras))):
            if any(_inside(path, root.resolve()) for root in PRODUCTION_ROOTS):
                raise RuntimeError(f"{environment} writable {role} targets production path: {path}")
            mount = _mount_identity(path)
            if mount and any(token in mount for token in (
                    "trading-ai_scraper-data", "My Passport/passport/trading-ai",
                    "trading-ai/scrapper")):
                raise RuntimeError(f"{environment} writable {role} uses production mount: {path}")
            marker = _marker_ancestor(path)
            if marker and _read_json(marker).get("environment") == "production":
                if (not clone_root or not _inside(path, clone_root) or
                        not _inside(marker, clone_root) or
                        clone.get("source_marker_sha256") !=
                        hashlib.sha256(marker.read_bytes()).hexdigest()):
                    raise RuntimeError(f"{environment} target has unverified production marker: {marker}")

    record = {
        "event": "runtime_writable_target_check",
        "environment": environment,
        "targets": {**{key: str(path) for key, path in core.items()},
                    "extra": [str(path) for path in extras]},
        "release_id": _public_id((identity or {}).get("release_id")),
        "git_commit": _public_id((identity or {}).get("git_commit") or
                                 os.environ.get("TRADING_AI_GIT_COMMIT")),
        "image_id": _public_id((identity or {}).get("image_id") or
                               os.environ.get("TRADING_AI_IMAGE_ID")),
        "clone_id": _public_id((clone or {}).get("clone_id")),
    }
    rendered = json.dumps(record, sort_keys=True)
    if rendered not in _logged:
        print(rendered, file=sys.stderr)
        _logged.add(rendered)
