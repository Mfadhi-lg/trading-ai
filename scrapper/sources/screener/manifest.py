"""Versioned Screener manifest loading and saved-fixture coverage audit."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "manifests" / "screener_company.yml"
VALID_STATUSES = {"capture", "metadata_only", "conditional", "deferred"}


def load_screener_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    """Load the JSON-form YAML manifest and validate its decision metadata."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("page_type") != "screener_company":
        raise ValueError("Screener manifest has the wrong page_type")
    if not manifest.get("manifest_version"):
        raise ValueError("Screener manifest has no version")

    approvals = {
        item["id"]
        for item in manifest.get("deferral_approvals", [])
        if item.get("approved") is True
    }
    decision_groups = (
        "regions",
        "profile_blocks",
        "tables",
        "document_lists",
        "controls",
        "chart_series",
        "expandable_schedules",
        "shareholder_drilldowns",
        "linked_components",
    )
    for group in decision_groups:
        rows = manifest.get(group)
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"Screener manifest group {group!r} is empty")
        codes: set[str] = set()
        for row in rows:
            status = row.get("status")
            if status not in VALID_STATUSES:
                raise ValueError(f"Invalid manifest status {status!r} in {group}")
            if not row.get("source"):
                raise ValueError(f"Manifest decision in {group} has no source")
            if status == "conditional" and not row.get("condition"):
                raise ValueError(f"Conditional manifest decision in {group} has no condition")
            if "presence_condition" in row and not row.get("presence_condition"):
                raise ValueError(
                    f"Manifest decision in {group} has an empty presence condition"
                )
            if status == "deferred" and row.get("deferral_approval") not in approvals:
                raise ValueError(f"Deferred manifest decision in {group} is not approved")
            code = row.get("code")
            if code:
                if code in codes:
                    raise ValueError(
                        f"Duplicate manifest component code in {group}: {code}"
                    )
                codes.add(code)
    return manifest


SHAREHOLDING_UNAVAILABLE = "Shareholding pattern is currently not available for this company."


def shareholding_unavailable(soup: BeautifulSoup, frequency: str) -> bool:
    """Require an explicit source notice in the matching empty container."""
    container = soup.select_one(f"section#shareholding #{frequency}-shp")
    return bool(
        container is not None
        and container.find("table") is None
        and container.get_text(" ", strip=True) == SHAREHOLDING_UNAVAILABLE
    )


def _is_optional(row: dict[str, Any]) -> bool:
    """True when the source may legitimately omit this component from a page.

    A decision is optional either because capture depends on an access or
    response condition (`status: conditional`) or because the source only
    renders the component for some companies (`presence_condition`).
    """
    return row.get("status") == "conditional" or bool(row.get("presence_condition"))


def _coverage_error(
    label: str, rows: list[dict[str, Any]], key: Any, actual: set[Any]
) -> str | None:
    """Compare a page inventory against a manifest group.

    A component the manifest does not know about is always an error: that is
    the uncovered source data this audit exists to catch. A component the
    manifest marks optional may be absent without an error.
    """
    allowed = {key(row) for row in rows}
    required = {key(row) for row in rows if not _is_optional(row)}
    if required <= actual <= allowed:
        return None
    return (
        f"{label} differ: "
        f"actual={sorted(actual)!r} "
        f"required={sorted(required)!r} "
        f"allowed={sorted(allowed)!r}"
    )


def _top_level_tables(section: Tag) -> list[Tag]:
    return [table for table in section.find_all("table") if table.find_parent("table") is None]


def _control_keys(soup: BeautifulSoup) -> set[str]:
    keys = {
        f"input:{node['id']}"
        for node in soup.select("#top input:not([type=hidden])[id], main input:not([type=hidden])[id]")
    }
    keys.update(
        f"options:{node['id']}"
        for node in soup.select("#top .options[id], main .options[id]")
    )
    keys.update(
        f"tab:{node['data-tab-id']}" for node in soup.select("main button[data-tab-id]")
    )
    keys.update(
        "swap:{}:{}".format(
            node["data-swap"], " ".join(node.get_text(" ", strip=True).split())
        )
        for node in soup.select("main button[data-swap]")
    )
    if soup.select_one("section#peers #benchmarks"):
        keys.add("group:benchmarks")
    return keys


def audit_screener_fixture_coverage(
    content: bytes, manifest: dict[str, Any] | None = None
) -> list[str]:
    """Return all differences between a saved company fixture and its manifest.

    The audit intentionally inventories semantic page components rather than all
    navigation/operational DOM. It covers the top data card, every main section,
    every top-level data table, prose blocks, document families, data filters,
    chart choices, and both kinds of click-to-expand datasets.
    """
    manifest = manifest or load_screener_manifest()
    soup = BeautifulSoup(content, "html.parser")
    unavailable = {
        frequency for frequency in ("quarterly", "yearly")
        if shareholding_unavailable(soup, frequency)
    }
    unavailable_table_codes = {f"{frequency}_shareholding" for frequency in unavailable}
    manifest = dict(manifest)
    manifest["tables"] = [
        dict(row, presence_condition="explicit source unavailable notice")
        if row.get("code") in unavailable_table_codes else row
        for row in manifest["tables"]
    ]
    manifest["shareholder_drilldowns"] = [
        dict(row, presence_condition="explicit source unavailable notice")
        if row["frequency"] in unavailable else row
        for row in manifest["shareholder_drilldowns"]
    ]
    errors: list[str] = []

    actual_regions = {"#top"} if soup.select_one("#top") else set()
    actual_regions.update(
        f"section#{node['id']}" for node in soup.select("main section[id]")
    )

    actual_profile = {
        " ".join(node.get_text(" ", strip=True).split())
        for node in soup.select("#top .company-profile .title")
    }

    actual_tables: set[tuple[str, int]] = set()
    for section in soup.select("main section[id]"):
        actual_tables.update(
            (str(section["id"]), index)
            for index, _ in enumerate(_top_level_tables(section))
        )

    actual_documents = {
        " ".join(node.get_text(" ", strip=True).split())
        for node in soup.select("section#documents h3")
    }

    actual_chart = {
        (
            " ".join(node.get_text(" ", strip=True).split()),
            str(node.get("value", "")),
        )
        for node in soup.select("section#chart button[name='metrics'][value]")
        if str(node.get("value", ""))
    }
    schedule_pattern = re.compile(r"showSchedule\('([^']+)',\s*'([^']+)'")
    actual_schedules: set[tuple[str, str]] = set()
    for node in soup.select("button[onclick*='Company.showSchedule']"):
        match = schedule_pattern.search(str(node.get("onclick", "")))
        if not match:
            errors.append("unparseable Company.showSchedule control")
        else:
            actual_schedules.add((match.group(2), match.group(1)))
    shareholder_pattern = re.compile(r"showShareholders\('([^']+)',\s*'([^']+)'")
    actual_shareholders: set[tuple[str, str]] = set()
    for node in soup.select("button[onclick*='Company.showShareholders']"):
        match = shareholder_pattern.search(str(node.get("onclick", "")))
        if not match:
            errors.append("unparseable Company.showShareholders control")
        else:
            actual_shareholders.add((match.group(2), match.group(1)))
    group_inventories = (
        ("regions", "regions", actual_regions, lambda row: row["selector"]),
        ("profile_blocks", "profile blocks", actual_profile, lambda row: row["heading"]),
        (
            "tables",
            "tables",
            actual_tables,
            lambda row: (row["section"], int(row["table_index"])),
        ),
        (
            "document_lists",
            "document lists",
            actual_documents,
            lambda row: row["heading"],
        ),
        ("controls", "controls", _control_keys(soup), lambda row: row["key"]),
        (
            "chart_series",
            "chart series",
            actual_chart,
            lambda row: (row["label"], row["request_metrics"]),
        ),
        (
            "expandable_schedules",
            "expandable schedules",
            actual_schedules,
            lambda row: (row["section"], row["schedule_key"]),
        ),
        (
            "shareholder_drilldowns",
            "shareholder drilldowns",
            actual_shareholders,
            lambda row: (row["frequency"], row["classification"]),
        ),
    )
    for group, label, actual, key in group_inventories:
        error = _coverage_error(label, manifest[group], key, actual)
        if error is not None:
            errors.append(error)

    for row in manifest["linked_components"]:
        if not _is_optional(row) and not soup.select_one(row["selector"]):
            errors.append(f"linked component selector is absent: {row['code']}")
    return errors
