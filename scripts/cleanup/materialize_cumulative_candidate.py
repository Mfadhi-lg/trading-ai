#!/usr/bin/env python3
"""Materialize the isolated C04 through C17.4 candidate from retained artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[2]
COMPOSITION = ROOT / "docs/cleanup/baselines/cumulative-candidate/composition.json"
IGNORED_PARTS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
}
FACADE_PATH = "artifacts/rating-link-refresh-20261001/refresh.py"
FACADE_SEED_MEMBER = "before/artifacts/rating-link-refresh-20261001/refresh.py"
MIGRATION_WORKFLOW = ".github/workflows/migration-lineage.yml"
MIGRATION_LEDGER_TEST = (
    "        run: python -m unittest scrapper.tests.test_migration_manifest -v\n"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_git(tree: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(tree), *args], capture_output=True, text=True
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result


def safe_relative(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe archive or patch path: {name!r}")
    return path


def excluded_from_source(name: str) -> bool:
    return any(part in IGNORED_PARTS for part in safe_relative(name).parts)


def extract_source_zip(source: Path, output: Path) -> None:
    extracted: set[PurePosixPath] = set()
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            relative = safe_relative(member.filename)
            if excluded_from_source(member.filename):
                continue
            if relative in extracted:
                raise ValueError(f"source archive repeats a file path: {member.filename}")
            extracted.add(relative)
            mode = (member.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise ValueError(f"source archive contains a symlink: {member.filename}")
            target = output.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))
            permissions = (member.external_attr >> 16) & 0o777
            target.chmod(permissions or 0o644)


def patch_blocks(patch: Path) -> list[tuple[str, bytes]]:
    raw = patch.read_bytes()
    lines = raw.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.startswith(b"diff --git ")]
    blocks: list[tuple[str, bytes]] = []
    if starts:
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(lines)
            block = b"".join(lines[start:end])
            header = lines[start].decode("utf-8").rstrip("\n")
            fields = header.split(" ", 3)
            if len(fields) != 4:
                raise ValueError(f"cannot parse patch header: {header}")
            old_path = fields[2][2:]
            new_path = fields[3][2:]
            selected = new_path if new_path != "/dev/null" else old_path
            safe_relative(selected)
            blocks.append((selected, block))
    else:
        index = 0
        while index < len(lines):
            if not lines[index].startswith(b"--- ") or index + 1 >= len(lines) or not lines[index + 1].startswith(b"+++ "):
                index += 1
                continue
            start = index
            old_path = lines[index][4:].decode("utf-8").rstrip("\r\n").split("\t", 1)[0]
            new_path = lines[index + 1][4:].decode("utf-8").rstrip("\r\n").split("\t", 1)[0]
            index += 2
            while index < len(lines):
                match = re.match(rb"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@", lines[index])
                if not match:
                    if lines[index].startswith(b"--- ") and index + 1 < len(lines) and lines[index + 1].startswith(b"+++ "):
                        break
                    index += 1
                    continue
                old_count = int(match.group(1) or b"1")
                new_count = int(match.group(2) or b"1")
                index += 1
                old_seen = new_seen = 0
                while index < len(lines) and (old_seen < old_count or new_seen < new_count):
                    marker = lines[index][:1]
                    if marker == b" ":
                        old_seen += 1
                        new_seen += 1
                    elif marker == b"-":
                        old_seen += 1
                    elif marker == b"+":
                        new_seen += 1
                    elif marker != b"\\":
                        raise ValueError(f"malformed unified hunk in {patch} at line {index + 1}")
                    index += 1
                if old_seen != old_count or new_seen != new_count:
                    raise ValueError(f"truncated unified hunk in {patch}")
            block = b"".join(lines[start:index])
            selected = new_path if new_path != "/dev/null" else old_path
            if selected.startswith(("a/", "b/")):
                selected = selected[2:]
            safe_relative(selected)
            blocks.append((selected, block))
    if raw.strip() and not blocks:
        raise ValueError(f"patch has no file sections: {patch}")
    return blocks


def git_apply_block(tree: Path, block_path: Path, block: bytes, *, reverse: bool = False) -> subprocess.CompletedProcess[str]:
    block_path.write_bytes(block)
    command = ["apply", "--index", "--check"]
    if reverse:
        command.append("--reverse")
    command.append(str(block_path))
    return run_git(tree, *command, check=False)


def apply_block(tree: Path, scratch: Path, chunk: str, path: str, block: bytes) -> str:
    patch_file = scratch / "current.patch"
    forward = git_apply_block(tree, patch_file, block)
    if forward.returncode == 0:
        run_git(tree, "apply", "--index", str(patch_file))
        return "applied"

    reverse = git_apply_block(tree, patch_file, block, reverse=True)
    if reverse.returncode == 0:
        return "already-present"

    if chunk == "C04.2" and path == MIGRATION_WORKFLOW:
        compose_c042_workflow(tree, block)
        return "composed-overlap"

    raise RuntimeError(
        f"cannot apply {chunk} patch for {path}; forward: "
        f"{forward.stderr.strip()}; reverse: {reverse.stderr.strip()}"
    )


def compose_c042_workflow(tree: Path, block: bytes) -> None:
    """Retain both C04.1's refinement and C04.2's adjacent CI step."""

    target = tree / MIGRATION_WORKFLOW
    text = target.read_text()
    additions = [
        line[1:].decode("utf-8")
        for line in block.splitlines(keepends=True)
        if line.startswith(b"+") and not line.startswith(b"+++")
    ]
    if not additions:
        raise RuntimeError("C04.2 workflow patch has no additions to compose")
    if all(line in text for line in additions):
        return
    if text.count(MIGRATION_LEDGER_TEST) != 1:
        raise RuntimeError("C04.2 workflow anchor is absent or ambiguous")
    anchor = MIGRATION_LEDGER_TEST
    text = text.replace(anchor, anchor + "".join(additions), 1)
    target.write_text(text)
    run_git(tree, "add", "--", MIGRATION_WORKFLOW)


def hydrate_c174_facade(tree: Path, harness: Path, expected_seed: str) -> str:
    target = tree / FACADE_PATH
    if target.exists():
        return "already-present"
    with tarfile.open(harness, "r") as archive:
        try:
            member = archive.getmember(FACADE_SEED_MEMBER)
        except KeyError as error:
            raise RuntimeError(f"C17.4 harness lacks {FACADE_SEED_MEMBER}") from error
        if not member.isfile():
            raise RuntimeError("C17.4 dated facade seed is not a regular file")
        stream = archive.extractfile(member)
        if stream is None:
            raise RuntimeError("cannot read C17.4 dated facade seed")
        data = stream.read()
    if sha256_bytes(data) != expected_seed:
        raise RuntimeError("C17.4 dated facade seed checksum mismatch")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    target.chmod(0o644)
    run_git(tree, "add", "--", FACADE_PATH)
    return "hydrated-from-harness"


def tree_manifest(tree: Path) -> dict[str, object]:
    records: list[dict[str, object]] = []
    for current, dirs, names in os.walk(tree, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name != ".git")
        for name in sorted(names):
            path = Path(current) / name
            if path.is_symlink() or not path.is_file():
                raise RuntimeError(f"materialized tree contains a non-regular file: {path}")
            relative = path.relative_to(tree).as_posix()
            data_hash = sha256_file(path)
            records.append(
                {
                    "path": relative,
                    "mode": stat.S_IMODE(path.stat().st_mode),
                    "size": path.stat().st_size,
                    "sha256": data_hash,
                }
            )
    records.sort(key=lambda item: str(item["path"]))
    digest = hashlib.sha256()
    for item in records:
        digest.update(str(item["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(f"{int(item['mode']):04o}".encode("ascii"))
        digest.update(b"\0")
        digest.update(str(item["size"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(item["sha256"]).encode("ascii"))
        digest.update(b"\n")
    return {
        "tree_sha256": digest.hexdigest(),
        "file_count": len(records),
        "total_bytes": sum(int(item["size"]) for item in records),
        "files": records,
    }


def build_tree(output: Path, composition: dict[str, object], root: Path) -> dict[str, object]:
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    baseline_record = composition["baseline"]
    assert isinstance(baseline_record, dict)
    baseline = root / str(baseline_record["path"])
    if sha256_file(baseline) != baseline_record["sha256"]:
        raise RuntimeError("source baseline checksum mismatch")

    scratch = output.parent / (output.name + ".patch-work")
    if scratch.exists():
        raise FileExistsError(f"patch workspace already exists: {scratch}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    try:
        extract_source_zip(baseline, output)
        run_git(output, "init", "--quiet")
        run_git(output, "add", "--all")

        facade_record = composition["dated_facade"]
        assert isinstance(facade_record, dict)
        harness = root / str(facade_record["harness"])
        if sha256_file(harness) != facade_record["harness_sha256"]:
            raise RuntimeError("C17.4 harness checksum mismatch")

        trace: list[dict[str, str]] = []
        scratch.mkdir()
        for patch_record in composition["patches"]:
            assert isinstance(patch_record, dict)
            patch = root / str(patch_record["path"])
            if sha256_file(patch) != patch_record["sha256"]:
                raise RuntimeError(f"patch checksum mismatch: {patch_record['path']}")
            chunk = str(patch_record["chunk"])
            if chunk == "C17.4":
                disposition = hydrate_c174_facade(
                    output, harness, str(facade_record["seed_sha256"])
                )
                trace.append({"chunk": chunk, "path": FACADE_PATH, "result": disposition})
            for path, block in patch_blocks(patch):
                disposition = apply_block(output, scratch, chunk, path, block)
                trace.append({"chunk": chunk, "path": path, "result": disposition})
        shutil.rmtree(scratch)
        git_dir = output / ".git"
        if not git_dir.is_dir():
            raise RuntimeError("temporary patch-application repository is missing")
        shutil.rmtree(git_dir)
        result = tree_manifest(output)
        facade_path = output / FACADE_PATH
        if not facade_path.is_file() or sha256_file(facade_path) != facade_record["candidate_sha256"]:
            raise RuntimeError("materialized C17.4 dated facade does not match its frozen candidate")
        result["patch_trace"] = trace
        result["dated_facade_sha256"] = sha256_file(facade_path)
        return result
    except Exception:
        if scratch.exists():
            shutil.rmtree(scratch)
        if output.exists():
            shutil.rmtree(output)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="new directory for the materialized source tree")
    parser.add_argument("--manifest", required=True, help="path for deterministic materialization evidence")
    parser.add_argument("--verify-repeat", action="store_true", help="build a second copy and compare its tree digest")
    args = parser.parse_args()

    composition = json.loads(COMPOSITION.read_text())
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = ROOT / manifest_path
    output = output.resolve()
    manifest_path = manifest_path.resolve()
    try:
        output_relative = output.relative_to(ROOT)
        manifest_path.relative_to(ROOT)
    except ValueError as error:
        raise ValueError("output and evidence paths must stay inside the repository") from error
    if not output_relative.parts or ".git" in output_relative.parts:
        raise ValueError("refusing an unsafe output directory")
    if output == manifest_path or output in manifest_path.parents:
        raise ValueError("materialization evidence must be outside the source tree")
    if manifest_path.exists():
        raise FileExistsError(f"evidence already exists: {manifest_path}")

    result = build_tree(output, composition, ROOT)
    repeat_digest = None
    repeat_trace_match = None
    if args.verify_repeat:
        with tempfile.TemporaryDirectory(prefix="cumulative-replay-", dir=output.parent) as temporary:
            repeat_output = Path(temporary) / "tree"
            repeat = build_tree(repeat_output, composition, ROOT)
            repeat_digest = repeat["tree_sha256"]
            repeat_trace_match = repeat["patch_trace"] == result["patch_trace"]
            if repeat_digest != result["tree_sha256"] or not repeat_trace_match:
                raise RuntimeError("independent materialization replay differs")

    baseline = composition["baseline"]
    evidence = {
        "schema": "cumulative-candidate-materialization-v1",
        "composition_manifest": COMPOSITION.relative_to(ROOT).as_posix(),
        "composition_sha256": sha256_file(COMPOSITION),
        "base_artifact": baseline["path"],
        "base_sha256": baseline["sha256"],
        "patches": composition["patches"],
        "tree_path": output_relative.as_posix(),
        "tree_sha256": result["tree_sha256"],
        "file_count": result["file_count"],
        "total_bytes": result["total_bytes"],
        "dated_facade_path": FACADE_PATH,
        "dated_facade_sha256": result["dated_facade_sha256"],
        "patch_trace": result["patch_trace"],
        "files": result["files"],
        "repeat_tree_sha256": repeat_digest,
        "repeat_trace_match": repeat_trace_match,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: evidence[key] for key in ("tree_path", "tree_sha256", "file_count", "total_bytes", "dated_facade_sha256", "repeat_tree_sha256")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
