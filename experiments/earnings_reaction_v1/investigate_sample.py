"""Reproduce the five-row TA-02A review from retained evidence, offline only."""
import csv
import hashlib
import json
from pathlib import Path

from .verification import Check, Outcome, candle_join, evidenced_calendar, publication_time

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/earnings-reaction-v1/ta02a"
SAMPLE = ROOT / "artifacts/earnings-reaction-v1/ta01/event-sample-manifest.json"
SELECTED = ("15191", "15192", "15833", "16266", "748")


def build_rows():
    evidence = json.loads((OUT / "source-evidence.json").read_text())
    if hashlib.sha256(SAMPLE.read_bytes()).hexdigest() != evidence["ta01_manifest_sha256"]:
        raise ValueError("TA-01 source manifest changed; review required")
    samples = {x["event"]["filing_id"]: x for x in json.loads(SAMPLE.read_text())["events"]}
    calendars = json.loads((OUT / "calendar-evidence.json").read_text())
    rows = []
    for fid in SELECTED:
        sample = samples[fid]
        event, filing = sample["event"], sample["retained_filing"]
        raw = json.loads(filing["raw_row_json"])
        reviewed = evidence["filings"][fid]
        ref = "source-evidence.json#/filings/" + fid
        page_seen = reviewed["official_page_observed"]
        source = Check(Outcome.VERIFIED if page_seen else Outcome.UNRESOLVED,
                       "official_filing_content_observed_via_web_tool_original_bytes_not_retained" if page_seen
                       else "official_filing_page_inaccessible_and_original_feed_blob_missing", ref)
        _, publication = publication_time(raw, field_evidence=Check(
            Outcome.UNRESOLVED, "original_feed_blob_missing_and_dated_announcement_lookup_inaccessible", ref))
        _, cal_check = evidenced_calendar(calendars[reviewed["calendar_window"]])
        checks = dict(source=source, publication=publication,
                      company=Check(Outcome.UNRESOLVED, "company_crosswalk_has_no_historical_validity", ref),
                      instrument=Check(Outcome.UNRESOLVED,
                          "security_version_starts_2026-08-06_after_event_no_historical_series_or_tradability_no_provider_record", ref),
                      session=Check(Outcome.BLOCKED, "publication_unverified;" + cal_check.reason, cal_check.source_ref),
                      relationship=Check(Outcome.UNRESOLVED,
                          "explicit_parent_link_missing" if event["kind"] == "amendment"
                          else "source_declared_original_not_earliest_earnings_disclosure_reviewed", ref),
                      period_scope=Check(Outcome.VERIFIED if page_seen else Outcome.UNRESOLVED,
                          "official_page_period_and_scope_match" if page_seen else "stored_row_only_original_page_unavailable", ref))
        join = candle_join(checks)
        security = sample["exchange_security_rows"][0]
        version = evidence["security_versions"][event["exchange_key"]]
        isin = reviewed.get("source_isin") or security["current_isin"]
        row = dict(filing_id=fid, filing_key=event["filing_key"], exchange=event["exchange"],
                   stored_company_id=event["identity"]["company_id"], symbol_candidate=event["exchange_key"],
                   company_name_stored=event["company_name_raw"], sector=reviewed["sector"],
                   isin_candidate=isin, isin_basis="official_filing_page" if page_seen else "later_security_master_only",
                   instrument_category_candidate="equity", series_candidate=version["series_or_group"],
                   upstox_key_candidate="NSE_EQ|" + isin, upstox_key_verified="",
                   historical_listing_date_reported_later=version["listing_date"],
                   security_version_valid_from=version["valid_from_date"],
                   corporate_action_concerns="event-date symbol/ISIN/series and suspension history unverified; adjustment history unverified",
                   period_end=event["period_end"], fiscal_label="Q4 FY2026" if event["result_period"] == "2026-03" else "Q1 FY2027",
                   reporting_scope=event["reporting_scope"], source_declared_kind=event["kind"],
                   resolved_kind="unresolved", related_filing_id="", parent_candidate=reviewed.get("parent_candidate", ""),
                   exchange_received_raw=raw.get("EXCHANGE RECEIVED TIME", ""),
                   broadcast_raw=raw.get("BROADCAST DATE/TIME", ""), revised_raw=raw.get("REVISED DATE/TIME", ""),
                   dissemination_raw=raw.get("EXCHANGE DISSEMINATION TIME", ""),
                   filing_date_raw=raw.get("FILING DATE", ""), stored_broadcast_at_utc=filing["broadcast_at_utc"],
                   scraper_discovered_at_utc=filing["discovered_at_utc"], earliest_verified_publication_at="",
                   original_feed_sha256=sample["source_blob"]["content_sha256"],
                   original_feed_hash_status="UNRESOLVED", original_feed_path=sample["source_blob"]["local_path"],
                   snapshot_ref=event["evidence"]["snapshot_ref"], official_details_url=raw["DETAILS"],
                   raw_source_ref=event["evidence"]["source_ref"],
                   raw_row_sha256=hashlib.sha256(filing["raw_row_json"].encode()).hexdigest(),
                   conditional_first_session=reviewed["conditional_first_session"],
                   conditional_phase=reviewed["conditional_phase"], first_eligible_session="",
                   calendar_status=cal_check.outcome.value,
                   focus_membership=sample["focus_membership"], focus_role="descriptive_only_not_return_label")
        for name, check in {**checks, "candle_join": join}.items():
            row[name + "_status"] = check.outcome.value
            row[name + "_reason"] = check.reason
            row[name + "_source_ref"] = check.source_ref
        rows.append(row)
    return rows


def main():
    rows = build_rows()
    with (OUT / "event-verification-manifest.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
