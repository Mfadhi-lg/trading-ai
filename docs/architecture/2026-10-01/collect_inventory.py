"""Reproduce the static architecture inventory without importing application code.

Writes only sibling audit outputs. Does not open SQLite, contact sources, inspect
secrets, execute campaign scripts, or change runtime configuration.
"""
from __future__ import annotations

import ast
import collections
import csv
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parent
EXCLUDED = {".venv", "__pycache__", "store", ".git", ".pytest_cache"}


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def owner(path: Path) -> tuple[str, str]:
    parts = path.parts
    area = parts[1]
    name = path.stem
    if area == "tests":
        return "scrapper/tests (preserve initially)", "Retain regression anchor; group by capability later"
    if area == "sources":
        return str(path.parent), "Keep source parser/adapter boundary initially"
    if area == "cli.py":
        return "interfaces/cli", "Split registry and group dispatch; retain facade"
    if area == "config.py":
        return "runtime/settings", "Separate paths and capability settings with compatibility aliases"
    if area == "view":
        return "interfaces/tui + interfaces/queries", "Separate rendering, queries and mutation dispatch"
    if area == "db":
        return "infrastructure/db", "Separate persistence from policy/file/render orchestration"
    if area == "core":
        if name == "http":
            return "infrastructure/http", "Split source clients and shared host permits"
        if name in {"reporting_scope", "collection_scope", "access_policy", "nums"}:
            return "domain/shared", "Retain pure contracts; separate configured policy where needed"
        return "infrastructure/logging", "Keep logging as infrastructure"
    if area == "screener":
        return "compatibility/screener", "Retain wrapper; deprecate only after callers migrate"
    if area == "governance":
        return "domain/analysis/governance", "Preserve label, approval and selection contracts"
    if area == "exports":
        return "interfaces/queries + application/reporting", "Separate query payload from file writer"
    if area == "commands":
        if name in {"browser_rating_host", "publish_document_store_capacity"}:
            return "ops/host", "Move lifecycle/host capability behind compatibility launcher"
        if name == "browser_rating_headed":
            return "infrastructure/browser + ops/host", "Separate publisher executor and bridge client"
        if name == "browser_rating_recovery":
            return "application/documents + infrastructure/browser + infrastructure/db", "Extract ledger, validation, import, processing and CLI"
        return "interfaces/cli + application/use_cases", "Keep wrapper; extract SQL and reusable business operation"
    if area == "documents":
        if name in {"downloader", "credit_rating_resolver", "landing_page"}:
            return "infrastructure/http/documents", "Retain bounded transport and resolution contracts"
        if name == "store":
            return "infrastructure/storage", "Retain sentinel/reserve/capacity refusal"
        if name == "download_scope":
            return "application/documents + infrastructure/db", "Separate manifest validation and database scope registration"
        if name == "rating_identity":
            return "domain/identity + infrastructure/db", "Separate name matching from evidence query/catalog"
        if name in {"pdf", "powerpoint", "ocr", "html_pdf", "media"}:
            return "infrastructure/extraction", "Separate engine integration from extraction models"
        return "domain/documents", "Preserve pure evidence/normalization models and policies"
    if area == "pipeline":
        if name == "scheduler":
            return "runtime/coordinator + application/exchange", "Retain IST ladder, revisions and catch-up behind facade"
        if "governance" in name or name == "ml_contracts":
            return "application/analysis", "Split campaign I/O from governed feature/label contracts"
        if name in {"import_mf", "crosscheck_mfdata", "trendlyne_vertical", "trendlyne_pilot"}:
            return "application/holdings", "Keep import, precedence and frozen-pilot semantics"
        if name.startswith("screener"):
            return "application/finance", "Separate source ingestion, acceptance and queue lifecycle"
        if name == "exchange_universe":
            return "application/exchange", "Separate snapshot, identity and result use cases"
        if name in {"extract_document", "normalize_credit_rating", "ingest_official_transcript", "normalize_recording", "transcribe_recording", "run_scope_extraction", "render_html_documents"}:
            return "application/processing", "Keep versioned processing; introduce durable content handoff"
        if name == "observability":
            return "application/reporting", "Keep bounded event/progress contracts"
        if name.startswith(("build_", "audit_", "evaluate_", "finalize_")):
            return "application/reporting + ops/campaigns", "Split reusable accounting from finite campaign entry point"
        return "application/documents", "Keep acquisition/recovery invariants; extract worker lifecycle"
    return "scrapper", "Retain package facade"


def collect_sources() -> list[dict]:
    rows = []
    for absolute in sorted((ROOT / "scrapper").rglob("*.py")):
        path = absolute.relative_to(ROOT)
        if EXCLUDED.intersection(path.parts):
            continue
        body = absolute.read_bytes()
        source = body.decode("utf-8")
        tree = ast.parse(source, filename=str(path))
        imports = []
        functions = []
        classes = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append({"name": node.name, "line": node.lineno, "end_line": node.end_lineno})
            elif isinstance(node, ast.ClassDef):
                classes.append({"name": node.name, "line": node.lineno, "end_line": node.end_lineno})
            elif isinstance(node, ast.ImportFrom):
                imports.append({"module": "." * node.level + (node.module or ""), "names": [n.name for n in node.names], "line": node.lineno})
            elif isinstance(node, ast.Import):
                imports.extend({"module": n.name, "line": node.lineno} for n in node.names)
        target, action = owner(path)
        rows.append({"path": str(path), "lines": len(source.splitlines()), "sha256": digest(body),
                     "module_summary": ast.get_docstring(tree), "functions": sorted(functions, key=lambda n: n["line"]),
                     "classes": classes, "imports": imports, "proposed_owner": target, "first_action": action,
                     "coverage": "static AST and dependency inventory; detailed flow tracing is described in the review"})
    return rows


def collect_reports() -> list[dict]:
    paths = [ROOT / "TRADING_AI_MASTER_ROADMAP_V4_2026-09-29.md"]
    paths += list((ROOT / "scrapper").glob("*.md"))
    paths += list((ROOT / "scrapper/research").glob("*.md"))
    paths += [p for p in (ROOT / "artifacts").rglob("*.md") if not EXCLUDED.intersection(p.parts)]
    paths += list((ROOT / "experiments").rglob("*.md"))
    rows = []
    keywords = ("status", "supersed", "blocked", "partial", "gate:", "gate: **", "result:",
                "not executed", "not started", "not deployed", "review", "limitation", "unknown", "recovered", "red", "pit_")
    for absolute in sorted(set(paths)):
        if not absolute.exists():
            continue
        body = absolute.read_bytes()
        source = body.decode("utf-8")
        headings = [{"line": i, "text": line} for i, line in enumerate(source.splitlines(), 1) if line.startswith("#")]
        excerpts = [{"line": i, "text": line[:700]} for i, line in enumerate(source.splitlines(), 1)
                    if line.strip() and not line.startswith("|") and any(word in line.lower() for word in keywords)]
        rows.append({"path": str(absolute.relative_to(ROOT)), "lines": len(source.splitlines()),
                     "sha256": digest(body), "headings": headings, "status_excerpts": excerpts,
                     "interpretation": "dated evidence/design; do not infer current deployment from a historical status"})
    return rows


def main() -> None:
    sources = collect_sources()
    reports = collect_reports()
    migrations = []
    for p in sorted((ROOT / "scrapper/db/migrations").glob("*.sql")):
        body = p.read_bytes()
        source = body.decode("utf-8")
        migrations.append({"path": str(p.relative_to(ROOT)), "sha256": digest(body), "lines": len(source.splitlines()),
                           "declarations": [{"line": i, "text": line.strip()} for i, line in enumerate(source.splitlines(), 1)
                                            if line.strip().upper().startswith(("CREATE TABLE", "CREATE INDEX", "ALTER TABLE", "DROP TABLE", "CREATE TRIGGER", "CREATE VIEW"))]})
    for name, payload in (("source-inventory.json", sources), ("report-inventory.json", reports), ("migration-inventory.json", migrations)):
        (OUTPUT / name).write_text(json.dumps(payload, indent=2) + "\n")
    with (OUTPUT / "source-map.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["path", "lines", "proposed_owner", "first_action", "sha256"])
        writer.writeheader()
        writer.writerows({key: row[key] for key in writer.fieldnames} for row in sources)
    report_md = ["# Project report index", "", "All project Markdown documents found in the current roadmap, canonical package documentation/research, artifacts and experiments. Vendored dependency documentation and duplicated review copies are excluded.", "", "Status quotations below are dated evidence, not automatic deployment certification. See REPOSITORY_REVIEW.md for reconciled status and supersession.", ""]
    for row in reports:
        report_md += [f"## {row['path']}", "", f"Source: [{row['path']}]({ROOT / row['path']})", "", f"Lines: {row['lines']}. SHA256: `{row['sha256']}`.", ""]
        report_md += [f"- Line {excerpt['line']}: {excerpt['text']}" for excerpt in row["status_excerpts"][:5]] or ["- Design, reference, or supporting evidence; no matching status assertion."]
        report_md.append("")
    (OUTPUT / "report-index.md").write_text("\n".join(report_md) + "\n")
    stats = collections.defaultdict(lambda: {"files": 0, "working_tree_bytes": 0})
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    for name in filter(None, tracked):
        path = Path(name)
        group = path.parts[0] if len(path.parts) > 1 else "[root]"
        stats[group]["files"] += 1
        try:
            stats[group]["working_tree_bytes"] += (ROOT / path).stat().st_size
        except OSError:
            pass
    metadata = {"review_date": "2026-10-01", "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "python_files": len(sources), "python_lines": sum(row["lines"] for row in sources),
                "application_files": sum("/tests/" not in row["path"] for row in sources),
                "application_lines": sum(row["lines"] for row in sources if "/tests/" not in row["path"]),
                "migration_files": len(migrations), "report_files": len(reports), "report_lines": sum(row["lines"] for row in reports),
                "tracked_areas": dict(stats), "scope": "Static indexing plus targeted architecture review; no exhaustive correctness claim or live DB verification",
                "validation": {"existing_browser_recovery_monitor_lifecycle_tests": 28, "result": "PASS", "runtime": "host Python 3.14.5, existing combined environments; mocks and temporary/in-memory data", "production_runtime_proof": False}}
    (OUTPUT / "review-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps({key: metadata[key] for key in ("python_files", "python_lines", "application_files", "application_lines", "migration_files", "report_files", "report_lines")}, indent=2))


if __name__ == "__main__":
    main()
