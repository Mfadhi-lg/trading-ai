"""Evidence-based issuer-host provenance reconciliation for acquired documents."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..config import DB_PATH, assert_configured_writable_targets
from ..db.database import connect, init_db


def _host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        value = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if value.startswith("www."):
        value = value[4:]
    return value or None


def issuer_host_matches(document_host: str, website_host: str) -> bool:
    """Match exact issuer domains and their subdomains, never sibling domains."""

    document = document_host.lower().rstrip(".")
    website = website_host.lower().rstrip(".")
    if document.startswith("www."):
        document = document[4:]
    if website.startswith("www."):
        website = website[4:]
    return document == website or document.endswith(f".{website}")


def reconcile_issuer_retrieval_provenance(
    conn: Any, *, dry_run: bool = False
) -> dict[str, Any]:
    websites: dict[int, set[str]] = defaultdict(set)
    for row in conn.execute(
        """SELECT DISTINCT fetch.company_id,profile.source_url
             FROM company_profile_links AS profile
             JOIN parse_artifacts AS parsed USING (parse_id)
             JOIN fetch_observations AS fetch USING (blob_id)
            WHERE profile.link_type='website' AND fetch.company_id IS NOT NULL"""
    ):
        host = _host(str(row["source_url"]))
        if host:
            websites[int(row["company_id"])].add(host)
    matched: list[dict[str, Any]] = []
    for row in conn.execute(
        """SELECT source.artifact_source_id,source.artifact_id,
                  artifact.company_id,artifact.artifact_type,source.document_url,
                  source.discovered_from_source,source.retrieval_source_type,
                  source.document_originator,source.automation_status
             FROM artifact_sources AS source
             JOIN document_artifacts AS artifact USING (artifact_id)
            WHERE artifact.artifact_type IN ('transcript','presentation')
            ORDER BY source.artifact_source_id"""
    ):
        document_host = _host(str(row["document_url"]))
        if not document_host:
            continue
        matched_website = next(
            (
                website
                for website in sorted(websites.get(int(row["company_id"]), ()))
                if issuer_host_matches(document_host, website)
            ),
            None,
        )
        if matched_website is None:
            continue
        matched.append(
            {
                "artifact_source_id": int(row["artifact_source_id"]),
                "artifact_id": int(row["artifact_id"]),
                "company_id": int(row["company_id"]),
                "artifact_type": str(row["artifact_type"]),
                "retrieval_host": document_host,
                "matched_company_website_host": matched_website,
                "discovery_provenance_preserved": str(row["discovered_from_source"]),
            }
        )
    changed_sources = 0
    changed_contents = 0
    if not dry_run:
        with conn:
            for item in matched:
                changed_sources += conn.execute(
                    """UPDATE artifact_sources
                          SET retrieval_source_type='ISSUER_DIRECT',
                              retrieval_host=?,is_primary_source=1,
                              automation_status=CASE
                                WHEN automation_status='BLOCKED_TERMS'
                                  THEN automation_status ELSE 'AVAILABLE' END
                        WHERE artifact_source_id=?
                          AND (retrieval_source_type IS NULL
                               OR retrieval_source_type='ISSUER_DIRECT')
                          AND (COALESCE(retrieval_source_type,'')<>'ISSUER_DIRECT'
                               OR COALESCE(retrieval_host,'')<>?
                               OR is_primary_source<>1
                               OR automation_status NOT IN ('AVAILABLE','BLOCKED_TERMS'))""",
                    (
                        item["retrieval_host"],
                        item["artifact_source_id"],
                        item["retrieval_host"],
                    ),
                ).rowcount
            changed_contents = conn.execute(
                """UPDATE artifact_contents AS content SET is_official=1
                    WHERE content.content_id IN (
                      SELECT attempt.content_id
                        FROM artifact_download_attempts AS attempt
                        JOIN artifact_download_attempt_sources AS linked
                          USING (download_attempt_id)
                        JOIN artifact_sources AS source USING (artifact_source_id)
                       WHERE attempt.result_status='validated'
                         AND source.retrieval_source_type='ISSUER_DIRECT'
                         AND attempt.content_id IS NOT NULL
                    ) AND content.is_official=0"""
            ).rowcount
    return {
        "mode": "dry_run" if dry_run else "execute",
        "known_company_website_hosts": sum(len(value) for value in websites.values()),
        "matched_source_count": len(matched),
        "changed_source_count": changed_sources,
        "changed_content_count": changed_contents,
        "matches": matched,
        "invariants": {
            "discovery_provenance_rewritten": False,
            "document_originator_rewritten": False,
            "only_transcript_or_presentation": all(
                item["artifact_type"] in {"transcript", "presentation"}
                for item in matched
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    assert_configured_writable_targets(args.output, db_path=args.db)
    conn = connect(args.db)
    try:
        init_db(conn)
        report = reconcile_issuer_retrieval_provenance(conn, dry_run=args.dry_run)
    finally:
        conn.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in report.items() if key != "matches"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
