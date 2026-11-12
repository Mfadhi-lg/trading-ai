"""Read-only observations. No application imports, runtime defaults or report writes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SHA = re.compile(r"[0-9a-f]{40}")
SENTINEL = ".trading_ai_document_store"
LEGACY = {"discovery", "scheduler", "market-cap-monitor", "exchange-universe",
          "exchange-results", "exchange-reconciliation", "worker", "daemon",
          "artifact-reconciler", "document-download-worker", "document-processing-worker",
          "rating-recovery-discovery", "rating-recovery-download", "credit-rating-worker-care",
          "credit-rating-worker-india-ratings", "credit-rating-worker-other",
          "browser-rating-crisil", "browser-rating-icra", "browser-rating-other", "browser-rating-extract"}
CURRENT_PROJECTS = {"trading-ai-nse", "trading-ai-phase4-routine", "trading-ai-current-financial-once"}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def claim(value=None, *, status="unknown", source="not selected", method="not probed", detail=""):
    return dict(value=value, status=status, source=source, method=method,
                observed_at_utc=utcnow(), detail=detail)


def run(args, cwd=None):
    # Never expose raw stdout/stderr from runtime tools (may contain secrets).
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=10,
                            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    if result.returncode:
        raise OSError("probe failed")
    return result.stdout.strip()


def path_id(path):
    """Stable correlation token; no private host paths in shared reports."""
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return "unavailable"
    return "sha256:" + hashlib.sha256(str(resolved).encode()).hexdigest()


def repository(root, intended):
    source = "selected repository"
    try:
        head = run(["git", "rev-parse", "HEAD"], root)
        target = run(["git", "rev-parse", "--verify", intended + "^{commit}"], root)
        dirty = bool(run(["git", "status", "--porcelain", "--untracked-files=normal"], root))
        if not SHA.fullmatch(head) or not SHA.fullmatch(target):
            raise ValueError("invalid revision")
        return claim(dict(source_commit=head, intended_commit=target, dirty=dirty),
                     status="warning" if dirty or head != target else "verified",
                     source=source, method="git rev-parse; git status (optional locks disabled)",
                     detail="Local source only; not deployment evidence. Intended ref: " + intended)
    except (OSError, ValueError, subprocess.SubprocessError):
        return claim(source=source, method="git", detail="Repository revision could not be resolved.")


def migrations(root, database):
    source = "scrapper/db/migrations/*.sql"
    try:
        expected = sorted(p.name for p in (root / "scrapper/db/migrations").glob("*.sql"))
        if not expected:
            raise ValueError("missing migration sources")
        manifest = json.loads((root / "docs/cleanup/baselines/C22.3/migration-manifest.json").read_text())
        historical = {entry["filename"] for entry in manifest["historical_identities"]}
    except (OSError, ValueError, KeyError, TypeError):
        return claim(source=source, method="filename lineage", detail="Expected lineage unavailable.")
    if database is None:
        return claim(dict(expected=expected), source=source, method="filename lineage",
                     detail="No database selected; applied schema is unknown.")
    source = "selected SQLite database " + path_id(database)
    method = "immutable SQLite read-only SELECT version FROM schema_migrations"
    try:
        database = database.resolve()
        if not stat.S_ISREG(database.stat().st_mode):
            raise ValueError("not a regular database")
        # Immutable opens never create SHM/journal files, but ignore WAL. Refuse
        # sidecars instead of presenting an incomplete ledger as current truth.
        if any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-journal", "-shm")):
            return claim(source=source, method=method,
                         detail="WAL/journal sidecar present; use an operator-created consistent snapshot.")
        uri = database.resolve().as_uri() + "?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.set_authorizer(lambda action, *args: sqlite3.SQLITE_OK if action in
                                      {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ} else sqlite3.SQLITE_DENY)
            applied = [row[0] for row in connection.execute("SELECT version FROM schema_migrations")]
        finally:
            connection.close()
        if not all(isinstance(name, str) for name in applied):
            raise ValueError("invalid ledger")
        applied_set = set(applied)
        pending = sorted(set(expected) - applied_set)
        unknown_count = len(applied_set - set(expected) - historical)
        matches = not pending and not unknown_count and len(applied) == len(applied_set)
        # Only repository-known names are emitted; corrupt ledger strings can
        # contain private data and must never be copied into a shared report.
        return claim(dict(backend="sqlite", identifier=path_id(database),
                          applied_count=len(applied), latest_known=max(applied_set & set(expected), default=None),
                          pending=pending, unknown_identity_count=unknown_count,
                          duplicate_count=len(applied) - len(applied_set), matches_expected=matches),
                     status="verified" if matches else "warning", source=source, method=method,
                     detail="Filename ledger only, not schema integrity or deployment proof. Target must remain quiescent during observation.")
    except FileNotFoundError:
        return claim(status="unavailable", source=source, method=method, detail="Selected database missing; nothing created.")
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        return claim(source=source, method=method, detail="Cannot safely read selected migration ledger.")


def documents(path, mount):
    if path is None:
        return claim(detail="No document store selected.")
    source = "selected document store " + path_id(path)
    method = "stat; directory read; required sentinel read; explicit mount ismount"
    try:
        if mount is not None:
            if not path.resolve().is_relative_to(mount.resolve()):
                return claim(status="warning", value="invalid_mount", source=source, method=method)
            mount.stat()
            if not os.path.ismount(mount):
                return claim(status="unavailable", value="unmounted", source=source, method=method)
        if not stat.S_ISDIR(path.stat().st_mode):
            return claim(status="unavailable", value="unavailable", source=source, method=method)
        with os.scandir(path) as entries:
            next(entries, None)
        with (path / SENTINEL).open("rb") as stream:
            stream.read(1)
        return claim(value="readable", status="verified" if mount else "warning", source=source,
                     method=method, detail="Read access and sentinel only; capacity, writable readiness and content integrity not tested. "
                     + ("Mount verified." if mount else "Mount identity unknown; select --document-mount."))
    except PermissionError:
        return claim(value="inaccessible", status="unavailable", source=source, method=method)
    except FileNotFoundError:
        return claim(value="unavailable", status="unavailable", source=source, method=method,
                     detail="Store, mount or required sentinel missing; absence alone does not prove unmounted.")
    except (OSError, RuntimeError):
        return claim(source=source, method=method, detail="Storage probe failed; state unknown.")


def container(name, intended):
    if not name:
        return claim(detail="No live container selected; deployed commit unknown.")
    method = "docker inspect: running flag and OCI revision label only"
    try:
        # Filter on the daemon side: never retrieve environment or credentials.
        output = run(["docker", "inspect", "--type", "container", "--format",
                      '{{json .State.Running}} {{json (index .Config.Labels "org.opencontainers.image.revision")}}', name])
        running, revision = output.split(" ", 1)
        revision = json.loads(revision)
        if running != "true" or not isinstance(revision, str) or not SHA.fullmatch(revision):
            raise ValueError("no running revision")
        return claim(dict(reported_commit=revision, matches_intended=revision == intended if intended else None), status="warning",
                     source="selected live container", method=method,
                     detail="Container metadata observed now; label is self-reported and does not attest running code or host dispatcher revision.")
    except (OSError, ValueError, subprocess.SubprocessError):
        return claim(source="selected live container", method=method,
                     detail="No running container revision could be verified.")


def dispatcher(enabled):
    if not enabled:
        return claim(detail="Live dispatcher not selected; historical bounded-mode descriptions are not health evidence.")
    method = "launchctl print gui/<uid>/com.trading-ai.automatic-acquisition"
    try:
        output = run(["launchctl", "print", f"gui/{os.getuid()}/com.trading-ai.automatic-acquisition"])
        # Only inspect the loaded argument block; other output can include logs
        # or environment values containing these strings.
        arguments = re.search(r"(?m)^\s*arguments = \{\n(.*?)^\s*\}", output, re.S | re.M)
        bounded = bool(arguments and re.search(r"(?m)^\s*/[^\n]*?/ops/activation/automatic_acquisition\.py\s*$", arguments[1])
                       and re.search(r"(?m)^\s*--tick\s*$", arguments[1]))
        return claim("bounded_dispatcher_loaded" if bounded else "unrecognized_loaded_configuration",
                     status="verified" if bounded else "warning", source="live launchd service", method=method,
                     detail="Loaded arguments only; schedule, admission gates, completion and health not certified.")
    except (OSError, subprocess.SubprocessError):
        return claim(source="live launchd service", method=method,
                     detail="Service missing, inaccessible or tool unavailable; cannot infer stopped/healthy.")


def workers(enabled):
    source = "ops/host/acquisition_lifecycle.py:containers; compose.yml; compose.browser-recovery.yml"
    if not enabled:
        return claim(dict(retired_roles=sorted(LEGACY), running_retired_roles=None), source=source,
                     method="repository policy; live inventory not selected")
    try:
        output = run(["docker", "ps", "--format",
                      '{{.Label "com.docker.compose.project"}} {{.Label "com.docker.compose.service"}}'])
        rows = [line.split() for line in output.splitlines()]
        selected = [row for row in rows if row and row[0].startswith("trading-ai")]
        complete = [row for row in selected if len(row) == 2]
        found = sorted({service for project, service in complete
                        if project not in CURRENT_PROJECTS and service in LEGACY})
        unclassified = sum(len(row) != 2 for row in selected) + sum(
            project not in CURRENT_PROJECTS and service not in LEGACY
            for project, service in complete)
        return claim(dict(retired_roles=sorted(LEGACY), running_retired_roles=found, unclassified_count=unclassified),
                     status="warning" if found or unclassified else "verified", source="live Docker compose service labels; " + source,
                     method="docker ps (running containers only)",
                     detail="Only listed legacy compose roles on selected Docker context; other processes/contexts unknown.")
    except (OSError, subprocess.SubprocessError):
        return claim(source=source, method="docker ps", detail="Live worker inventory unavailable.")


def previous(path, max_age, now):
    if path is None:
        return claim(detail="No previous report selected; last verified production state unknown.")
    source = "selected previous report " + path_id(path)
    try:
        payload = json.loads(path.read_text())
        timestamp = datetime.fromisoformat(payload["generated_at_utc"])
        age = (now - timestamp).total_seconds()
        if payload.get("report_version") != 1 or age < 0:
            raise ValueError("invalid report")
        return claim(dict(generated_at_utc=timestamp.isoformat(), age_seconds=age,
                          stale=age > max_age), status="warning", source=source, method="saved report timestamp",
                     detail="Historical observation only, including when recent. Runtime claims must be refreshed; never promoted to live status.")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return claim(source=source, method="saved report timestamp", detail="Previous report missing, malformed or future-dated.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intended-revision", default="HEAD", help="Explicit intended git ref; default is local HEAD, not remote main")
    parser.add_argument("--database", type=Path, help="Explicit quiescent SQLite file or consistent snapshot; never auto-detected")
    parser.add_argument("--document-store", type=Path)
    parser.add_argument("--document-mount", type=Path, help="Expected mount enclosing document store")
    parser.add_argument("--container", help="Observe a selected live container's OCI revision label")
    parser.add_argument("--dispatcher", action="store_true", help="Read loaded launchd dispatcher arguments")
    parser.add_argument("--workers", action="store_true", help="Read running Docker compose service labels")
    parser.add_argument("--previous-report", type=Path)
    parser.add_argument("--max-age-seconds", type=int, default=3600)
    args = parser.parse_args(argv)
    if args.max_age_seconds < 0 or (args.document_mount and not args.document_store):
        parser.error("Age must be nonnegative; --document-mount requires --document-store")
    now = datetime.now(timezone.utc)
    repo = repository(ROOT, args.intended_revision)
    report = dict(report_version=1, generated_at_utc=now.isoformat(),
                  freshness_policy=dict(max_age_seconds=args.max_age_seconds, live_claims_are_point_in_time=True),
                  claims=dict(repository=repo,
                              deployed_revision=container(args.container, (repo["value"] or {}).get("intended_commit")),
                              schema=migrations(ROOT, args.database),
                              dispatcher=dispatcher(args.dispatcher), workers=workers(args.workers),
                              document_store=documents(args.document_store, args.document_mount),
                              last_observation=previous(args.previous_report, args.max_age_seconds, now),
                              historical_snapshots=claim(["docs/STATUS.md (3 October 2026)",
                                                         "docs/operations/operational-recovery-20261022.md (22 October 2026)"],
                                  status="warning", source="repository documentation", method="dated snapshot classification",
                                  detail="Preserved historical evidence; no current runtime claim.")))
    print(json.dumps(report, indent=2, sort_keys=True))
    # Exit status denotes successful report generation, never overall health.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
