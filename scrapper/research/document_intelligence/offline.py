"""Executable offline slice: local originals -> D03 -> validated JSONL export.

No DB connections, workers, schedulers or provider clients. Socket connect/bind
and DNS are denied before parsers load. Output is new-only and original bytes are
hash-checked again after processing. Exports are derived artifacts, not a new DB.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import fields
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import socket
import time
from unittest.mock import patch

from .schema import DocumentContext
from .extract import extract_intelligence
from .evidence import validate_evidence
from .temporal import eligible_at, compare_guidance


@contextmanager
def deny_network():
    def denied(*args, **kwargs):
        raise RuntimeError("Phase 5 offline execution forbids network access")
    with patch.object(socket.socket, "connect", denied), patch.object(socket.socket, "connect_ex", denied), \
            patch.object(socket.socket, "bind", denied), patch.object(socket, "getaddrinfo", denied), \
            patch.object(socket, "create_connection", denied), patch.object(socket.socket, "sendto", denied):
        yield


class DisabledOcr:
    name, version, language = "disabled-offline", "1", "eng"

    def extract_text(self, image):
        return "", None


def extract_local(body, suffix, *, ocr=False):
    from ...documents.extraction import DOCUMENT_PARSER_NAME, DOCUMENT_PARSER_VERSION
    from ...documents.ocr import TesseractOcr
    engine = TesseractOcr() if ocr else DisabledOcr()
    if suffix == ".pdf":
        from ...documents.pdf import extract_pdf
        extracted = extract_pdf(body, ocr_engine=engine)
    elif suffix in {".ppt", ".pptx"}:
        from ...documents.powerpoint import extract_powerpoint
        from ...documents.downloader import PPT, PPTX
        extracted = extract_powerpoint(body, media_type=PPTX if suffix == ".pptx" else PPT, ocr_engine=engine)
    elif suffix in {".html", ".htm"}:
        from ...documents.html import extract_rating_html
        extracted = extract_rating_html(body)
    else:
        raise ValueError(f"Unsupported local format: {suffix}")
    result = extracted.to_dict()
    if suffix == ".pptx":
        from .charts import enrich_pptx_charts
        result = enrich_pptx_charts(body, result)
    result.update(parser_name=DOCUMENT_PARSER_NAME, parser_version=DOCUMENT_PARSER_VERSION,
                  ocr_engine=engine.name, ocr_engine_version=engine.version)
    return result


def _input_path(root, raw):
    path = (root / raw).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Inputs must be local files within the manifest directory")
    return path


def run(manifest_path, output, *, reuse_canonical=False, ocr=False, decision_at=None, focus_disclosures=False):
    started = time.perf_counter()
    cpu_started = time.process_time()
    manifest_path, output = Path(manifest_path).resolve(), Path(output).resolve()
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    if not 1 <= len(manifest["documents"]) <= 12:
        raise ValueError("Offline slice is bounded to 1–12 documents")
    if output.exists() or output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError("Use a new output directory separate from immutable inputs")
    # In addition to new-only output, reject production stores and external
    # document volumes even when the requested leaf directory does not exist.
    from ...config import STORE, DOCUMENT_STORE, EXPORTS, DB_PATH
    protected_roots = [STORE.resolve(), DOCUMENT_STORE.resolve(), EXPORTS.resolve(), DB_PATH.parent.resolve()]
    if any(output.is_relative_to(p) for p in protected_roots) or output.is_relative_to(Path("/Volumes")):
        raise ValueError("Phase 5 output must not target a production store or volume")
    for row in manifest["documents"]:
        original = _input_path(root, row["path"])
        if original.stat().st_size > 50 * 1024 * 1024:
            raise ValueError("Offline source exceeds 50 MiB bound")
        if hashlib.sha256(original.read_bytes()).hexdigest() != row["content_sha256"]:
            raise ValueError("Original content hash mismatch")
    output.mkdir(parents=True, exist_ok=False)
    all_records, documents = [], []
    context_keys = {f.name for f in fields(DocumentContext)}
    with deny_network():
        for row in manifest["documents"]:
            before = time.perf_counter()
            original = _input_path(root, row["path"])
            body = original.read_bytes()
            context = DocumentContext(**{k: v for k, v in row.items() if k in context_keys})
            if reuse_canonical:
                retained_path = _input_path(root, row["canonical_path"])
                if hashlib.sha256(retained_path.read_bytes()).hexdigest() != row.get("canonical_file_sha256"):
                    raise ValueError("Retained canonical payload hash mismatch")
                retained = json.loads(retained_path.read_text())
                if (retained["content"]["content_sha256"] != context.content_sha256
                        or f"artifact:{retained['artifact']['artifact_id']}" != context.document_id
                        or retained["content"]["content_id"] != context.content_id):
                    raise ValueError("Retained extraction belongs to a different original identity")
                extraction = retained["extraction"]
            else:
                extraction = extract_local(body, original.suffix.lower(), ocr=ocr)
            records, diagnostics = extract_intelligence(context, extraction, focus_disclosures=focus_disclosures)
            if not all(validate_evidence(r, extraction) for r in records):
                raise ValueError("Evidence validation failed after reconciliation")
            if hashlib.sha256(original.read_bytes()).hexdigest() != context.content_sha256:
                raise ValueError("Original bytes changed during run")
            name = f"{context.document_type}-{context.content_sha256[:12]}"
            (output / (name + ".canonical.json")).write_text(json.dumps(extraction, indent=2) + "\n")
            (output / (name + ".evidence.jsonl")).write_text("".join(json.dumps(r.to_dict(), sort_keys=True) + "\n" for r in records))
            documents.append(dict(document=context.to_dict(), original_bytes=len(body), pages=len(extraction["pages"]),
                seconds=round(time.perf_counter() - before, 6), record_count=len(records),
                statuses=dict(Counter(r.status for r in records)), diagnostics=diagnostics))
            all_records.extend(records)
    if decision_at:
        from .research_features import public_pit_readiness
        eligible = [r.to_dict() for r in all_records if eligible_at(r, decision_at, records=all_records)
            and (not focus_disclosures or public_pit_readiness(r.source,decision_at)['eligible'])]
        (output / "point-in-time.json").write_text(json.dumps({"decision_at": decision_at, "eligible": eligible,
            'availability_basis':'certified_public_version_and_capture' if focus_disclosures else 'capture_safe_only'}, indent=2) + "\n")
    comparisons = []
    comparison_cutoff = decision_at or datetime.now(timezone.utc).isoformat()
    for current in all_records:
        if current.statement_kind not in {"GUIDANCE", "TARGET"} or (current.speaker or {}).get("role") != "management":
            continue
        earlier = [r for r in all_records if r.metric == current.metric and r.record_id != current.record_id
                   and r.source["content_sha256"] != current.source["content_sha256"]
                   and r.source.get("company_id") == current.source.get("company_id")
                   and r.statement_kind in {"GUIDANCE", "TARGET"}]
        if earlier:
            comparisons.extend(compare_guidance(r, current, decision_at=comparison_cutoff) for r in earlier)
        else:
            comparisons.append({"status": "NOT_FOUND", "change": "UNKNOWN", "current_record_id": current.record_id,
                "previous_record_id": None, "evidence": [[], current.evidence],
                "reason": "no_earlier_matching_statement_in_supplied_offline_sample", "decision_at": comparison_cutoff})
    (output / "guidance-comparisons.json").write_text(json.dumps(comparisons, indent=2) + "\n")
    usage = resource.getrusage(resource.RUSAGE_SELF)
    report = dict(schema_version="phase5-offline-run-v1", run_at=datetime.now(timezone.utc).isoformat(),
        manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(), manifest=str(manifest_path),
        execution={"network": "denied", "database_connections": 0, "external_model_calls": 0,
                   "production_changes": 0, "canonical_mode": "retained" if reuse_canonical else "fresh",
                   "ocr": "local_tesseract" if ocr else "disabled", "immutable_originals_rechecked": True},
        measurements={"wall_seconds": round(time.perf_counter() - started, 6),
            "cpu_seconds": round(time.process_time() - cpu_started, 6),
            "peak_rss_bytes": usage.ru_maxrss if platform.system() == "Darwin" else usage.ru_maxrss * 1024,
            "machine": platform.machine(), "platform": platform.platform(), "python": platform.python_version(),
            "logical_cpu_count": os.cpu_count(), "gpu_used": False},
        statuses=dict(Counter(r.status for r in all_records)), documents=documents)
    (output / "run.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse-canonical", action="store_true")
    parser.add_argument("--focus-disclosures", action="store_true", help="Extract the five investment disclosure categories")
    parser.add_argument("--ocr", action="store_true", help="Enable installed local Tesseract; OCR values remain unverified")
    parser.add_argument("--decision-at", help="Aware UTC timestamp for conservative point-in-time projection")
    args = parser.parse_args()
    report = run(args.manifest, args.output, reuse_canonical=args.reuse_canonical, ocr=args.ocr, decision_at=args.decision_at, focus_disclosures=args.focus_disclosures)
    print(json.dumps({"output": str(args.output), "measurements": report["measurements"], "statuses": report["statuses"]}, indent=2))


if __name__ == "__main__":
    main()
