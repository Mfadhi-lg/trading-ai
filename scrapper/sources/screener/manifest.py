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
    errors: list[str] = []

    actual_regions = {"#top"} if soup.select_one("#top") else set()
    actual_regions.update(
        f"section#{node['id']}" for node in soup.select("main section[id]")
    )
    expected_regions = {row["selector"] for row in manifest["regions"]}
    if actual_regions != expected_regions:
        errors.append(
            f"regions differ: actual={sorted(actual_regions)!r} expected={sorted(expected_regions)!r}"
        )

    actual_profile = {
        " ".join(node.get_text(" ", strip=True).split())
        for node in soup.select("#top .company-profile .title")
    }
    expected_profile = {row["heading"] for row in manifest["profile_blocks"]}
    if actual_profile != expected_profile:
        errors.append(
            f"profile blocks differ: actual={sorted(actual_profile)!r} expected={sorted(expected_profile)!r}"
        )

    actual_tables: set[tuple[str, int]] = set()
    for section in soup.select("main section[id]"):
        actual_tables.update(
            (str(section["id"]), index)
            for index, _ in enumerate(_top_level_tables(section))
        )
    expected_tables = {
        (row["section"], int(row["table_index"])) for row in manifest["tables"]
    }
    if actual_tables != expected_tables:
        errors.append(
            f"tables differ: actual={sorted(actual_tables)!r} expected={sorted(expected_tables)!r}"
        )

    actual_documents = {
        " ".join(node.get_text(" ", strip=True).split())
        for node in soup.select("section#documents h3")
    }
    allowed_documents = {row["heading"] for row in manifest["document_lists"]}
    required_documents = {
        row["heading"]
        for row in manifest["document_lists"]
        if row["status"] != "conditional"
    }
    if (
        not required_documents <= actual_documents
        or not actual_documents <= allowed_documents
    ):
        errors.append(
            "document lists differ: "
            f"actual={sorted(actual_documents)!r} "
            f"required={sorted(required_documents)!r} "
            f"allowed={sorted(allowed_documents)!r}"
        )

    actual_controls = _control_keys(soup)
    expected_controls = {row["key"] for row in manifest["controls"]}
    if actual_controls != expected_controls:
        errors.append(
            f"controls differ: actual={sorted(actual_controls)!r} expected={sorted(expected_controls)!r}"
        )

    actual_chart = {
        (
            " ".join(node.get_text(" ", strip=True).split()),
            str(node.get("value", "")),
        )
        for node in soup.select("section#chart button[name='metrics'][value]")
        if str(node.get("value", ""))
    }
    expected_chart = {
        (row["label"], row["request_metrics"]) for row in manifest["chart_series"]
    }
    if actual_chart != expected_chart:
        errors.append(
            f"chart series differ: actual={sorted(actual_chart)!r} expected={sorted(expected_chart)!r}"
        )

    schedule_pattern = re.compile(r"showSchedule\('([^']+)',\s*'([^']+)'")
    actual_schedules: set[tuple[str, str]] = set()
    for node in soup.select("button[onclick*='Company.showSchedule']"):
        match = schedule_pattern.search(str(node.get("onclick", "")))
        if not match:
            errors.append("unparseable Company.showSchedule control")
        else:
            actual_schedules.add((match.group(2), match.group(1)))
    allowed_schedules = {
        (row["section"], row["schedule_key"])
        for row in manifest["expandable_schedules"]
    }
    required_schedules = {
        (row["section"], row["schedule_key"])
        for row in manifest["expandable_schedules"]
        if not row.get("presence_condition")
    }
    if not required_schedules <= actual_schedules or not actual_schedules <= allowed_schedules:
        errors.append(
            "expandable schedules differ: "
            f"actual={sorted(actual_schedules)!r} "
            f"required={sorted(required_schedules)!r} "
            f"allowed={sorted(allowed_schedules)!r}"
        )

    shareholder_pattern = re.compile(r"showShareholders\('([^']+)',\s*'([^']+)'")
    actual_shareholders: set[tuple[str, str]] = set()
    for node in soup.select("button[onclick*='Company.showShareholders']"):
        match = shareholder_pattern.search(str(node.get("onclick", "")))
        if not match:
            errors.append("unparseable Company.showShareholders control")
        else:
            actual_shareholders.add((match.group(2), match.group(1)))
    allowed_shareholders = {
        (row["frequency"], row["classification"])
        for row in manifest["shareholder_drilldowns"]
    }
    required_shareholders = {
        (row["frequency"], row["classification"])
        for row in manifest["shareholder_drilldowns"]
        if not row.get("presence_condition")
    }
    if (
        not required_shareholders <= actual_shareholders
        or not actual_shareholders <= allowed_shareholders
    ):
        errors.append(
            "shareholder drilldowns differ: "
            f"actual={sorted(actual_shareholders)!r} "
            f"required={sorted(required_shareholders)!r} "
            f"allowed={sorted(allowed_shareholders)!r}"
        )

    for row in manifest["linked_components"]:
        presence_is_conditional = bool(row.get("presence_condition"))
        if (
            row["status"] != "conditional"
            and not presence_is_conditional
            and not soup.select_one(row["selector"])
        ):
            errors.append(f"linked component selector is absent: {row['code']}")
    return errors
