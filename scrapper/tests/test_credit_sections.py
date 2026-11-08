"""Offline correctness tests for compact section-aware credit extraction."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scrapper.research.document_intelligence import credit_candidate_provider as provider
from scrapper.research.document_intelligence import credit_fresh
from scrapper.research.document_intelligence import credit_sections as sections
from scrapper.research.document_intelligence.evidence import canonical_hash
from scrapper.research.document_intelligence.schema import DocumentContext


class CreditSectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ctx = DocumentContext(document_id="section-fixture", company_name="Example Limited",
            content_sha256="a" * 64, document_type="credit_rating", report_date="2026-05-01", symbol="EXAMPLE")
        page1 = ("Example Limited rating rationale\nRationale\nRevenue rose in FY2026 because volumes increased, while margin improved on product mix.\n"
                 "The stable outlook assumes working-capital days improve and profitability remains healthy.\n"
                 "Funding for the expansion will use debt only if internal accruals are insufficient.\nWhile execution")
        page2 = (" remains on schedule, delayed commissioning could weaken liquidity.\nCredit strengths\n"
                 "The company has diverse customers and four products. Inventory was 90 days in FY2026 versus 70 days in FY2025.\n"
                 "Liquidity position: Adequate\nCash was Rs. 8 crore at March 31, 2026 and limits were highly utilised.\n")
        self.ext = {"extraction_status": "ok", "parser_name": "synthetic", "parser_version": "1", "pages": [
            {"page_or_slide_number": 1, "selected_text": page1, "selected_text_origin": "native", "elements": []},
            {"page_or_slide_number": 2, "selected_text": page2, "selected_text_origin": "native", "elements": [
                {"element_position": 0, "text_origin": "native", "rows": [["Metric", "FY2026", "FY2025"], ["Inventory days", "90", "70"]], "bbox": [0, 0, 1, 1]}]},
        ]}
        self.catalog = provider.build_region_catalog(self.ctx, self.ext, version=provider.FULL_VERSION)
        self.sidecar, self.manifest = sections.build_section_material(self.ctx, self.ext, self.catalog)
        self.requests = {key: sections.build_section_request(self.ctx, key, self.sidecar) for key in sections.ASSIGNMENTS}

    def atom(self, assignment, atom_id, field, statement, support, **changes):
        value = {"id": atom_id, "disclosure_category": field.split(".")[0], "field": field,
            "statement": statement, "value": None, "unit": None, "period": None,
            "period_kind": "unknown", "accounting_scope": None, "attribution": "Rating agency",
            "statement_kind": "assessment", "conditions": [], "qualifications": [],
            "support_region_ids": support}
        value.update(changes)
        self.assertIn(field, self.requests[assignment]["fields"])
        return value

    def save_bundle(self, assignment, atoms):
        raw = json.dumps({"assignment_id": assignment, "disclosures": atoms}).encode()
        bundle = provider.generate_candidates(lambda _request: (raw, {"model": "synthetic", "inference_calls": 0}), self.requests[assignment])
        path = self.root / f"{assignment}.json"
        path.write_text(json.dumps(bundle))
        return path, bundle

    def test_complete_accounting_compact_resolution_and_cross_page(self):
        self.assertEqual(self.manifest["coverage"]["source_text_coverage_pct"], 100.0)
        self.assertEqual(self.manifest["coverage"]["physical_pages"], 2)
        self.assertEqual(self.manifest["coverage"]["accounted_pages"], 2)
        passages = [r for r in self.sidecar["compact_regions"] if r["kind"] == "passage"]
        self.assertEqual(sum(len(r["text"]) for r in passages), sum(len(p["selected_text"]) for p in self.ext["pages"]))
        continuation = next(r for r in passages if r["page"] == 2)
        self.assertIsNotNone(continuation["continues_from"])
        self.assertEqual(len(self.sidecar["source_regions"]), len(self.catalog["regions"]))
        for region in self.sidecar["compact_regions"]:
            for evidence in region["evidence"]:
                self.assertEqual(evidence["validation"], "EXACT_SOURCE_LOCATOR_VALIDATED")

    def test_long_passage_periods_conditions_multi_region_and_duplicate_merge(self):
        page1 = next(r["id"] for r in self.sidecar["compact_regions"] if r["kind"] == "passage" and r["page"] == 1 and "stable outlook" in r["text"].lower())
        page2 = next(r["id"] for r in self.sidecar["compact_regions"] if r["kind"] == "passage" and r["page"] == 2 and "remains on schedule" in r["text"])
        outlook = self.atom("A", "outlook", "assessment.agency_expectations",
            "The stable outlook assumes working-capital days improve and profitability remains healthy.", [page1],
            statement_kind="assumption", qualifications=["Outlook assumption, not a historical fact"])
        duplicate = deepcopy(outlook); duplicate["id"] = "same-outlook"
        funding = self.atom("D", "funding", "assessment.capex_and_execution_risks",
            "Expansion funding will use debt only if internal accruals are insufficient, and delay could weaken liquidity.",
            [page1, page2], statement_kind="expectation", conditions=["Internal accruals are insufficient"],
            qualifications=["Delay could weaken liquidity"])
        inventory = self.atom("C", "inventory26", "assessment.working_capital_intensity",
            "Inventory was 90 days in FY2026.", [page2], value="90", unit="days", period="FY2026",
            period_kind="historical_period", statement_kind="historical_fact")
        prior = deepcopy(inventory); prior.update(id="inventory25", statement="Inventory was 70 days in FY2025.", value="70", period="FY2025")
        paths = []
        for key, atoms in (("A", [outlook]), ("D", [duplicate, funding]), ("C", [inventory, prior])):
            path, _ = self.save_bundle(key, atoms); paths.append(path)
        claims, packet = sections.load_section_bundles(paths, self.ctx, self.ext, self.sidecar, self.requests)
        children = [c for c in claims if "atomic_key" in c]
        self.assertEqual(len(children), 4)  # duplicate outlook merged across A/D
        merged = next(c for c in packet["candidates"] if c["statement"].startswith("The stable outlook"))
        self.assertEqual(merged["source_assignments"], ["A", "D"])
        conditional = next(c for c in children if c["statement"].startswith("Expansion funding"))
        self.assertEqual(conditional["conditions"], ["Internal accruals are insufficient"])
        self.assertEqual({e["page"] for e in conditional["evidence"]}, {1, 2})
        self.assertEqual({c["period"] for c in children if c.get("unit") == "days"}, {"FY2025", "FY2026"})
        self.assertTrue(all(c["evidence_decision"] == "NEEDS_REVIEW" for c in children))

    def test_unsupported_withheld_unresolved_not_undisclosed_and_replay(self):
        page = next(r["id"] for r in self.sidecar["compact_regions"] if r["kind"] == "passage" and "Liquidity position" in r["text"])
        good = self.atom("D", "cash", "liquidity.cash_and_liquid_balances", "Cash was Rs. 8 crore at March 31, 2026.", [page],
            value="8", unit="Rs. crore", period="March 31, 2026", period_kind="as_of", statement_kind="historical_fact")
        bad = deepcopy(good); bad.update(id="invented", statement="Cash was Rs. 80 crore.", support_region_ids=["missing"])
        path, bundle = self.save_bundle("D", [good, bad])
        first, packet1 = sections.load_section_bundles([path], self.ctx, self.ext, self.sidecar, self.requests)
        second, packet2 = sections.load_section_bundles([path], self.ctx, self.ext, self.sidecar, self.requests)
        self.assertEqual(first, second); self.assertEqual(packet1, packet2)
        self.assertTrue(any(c["evidence_decision"] == "WITHHELD" for c in first))
        ledger = sections.section_coverage_ledger(self.manifest, self.requests, [json.loads(path.read_text())])
        self.assertEqual(ledger["assignments"]["A"]["response_state"], "UNRESOLVED_EXTRACTION")
        self.assertEqual(ledger["assignments"]["A"]["absence_state"], "NOT_EVALUATED")
        self.assertTrue(all(entry["absence_state"] != "NOT_DISCLOSED" for entry in ledger["assignments"].values()))
        review = {"source_sha256": self.ctx.content_sha256, "canonical_sha256": canonical_hash(self.ext),
                  "bundle_set_sha256": packet1["bundle_set_sha256"], "reviews": {}}
        candidate = packet1["candidates"][0]
        review["reviews"][candidate["atomic_key"]] = {"candidate_sha256": canonical_hash(candidate),
            "decision": "SUPPORTED", "reason": "Independent synthetic source review"}
        reviewed, _ = sections.load_section_bundles([path], self.ctx, self.ext, self.sidecar, self.requests, reviews=review)
        self.assertTrue(any(c["evidence_decision"] == "SUPPORTED" for c in reviewed))
        prepared = self.root / "prepared"; prepared.mkdir()
        (prepared / "section-manifest.json").write_text(json.dumps(self.manifest))
        output = self.root / "admitted"
        fake_freeze = {"section_manifest_sha256": canonical_hash(self.manifest)}
        # Materialize the pinned review and exercise the actual section admission/export facade.
        (self.root/"review.json").write_text(json.dumps(review))
        with patch.object(credit_fresh, "verify_sections", return_value=(fake_freeze, self.ctx, self.ext, self.sidecar, self.requests)):
            exported = credit_fresh.admit_sections(prepared, [path], output, self.root/"review.json")
        self.assertTrue(any(c["evidence_decision"] == "SUPPORTED" for c in exported))
        self.assertTrue((output/"export"/"example-credit-intelligence.json").exists())
        # Raw response and bundle hashes are replay-pinned.
        changed = json.loads(path.read_text()); changed["raw_response_hex"] += "20"; path.write_text(json.dumps(changed))
        with self.assertRaises(ValueError):
            sections.load_section_bundles([path], self.ctx, self.ext, self.sidecar, self.requests)

    def test_v2_intake_rejects_scope_not_established_for_facility_rating(self):
        page = next(r["id"] for r in self.sidecar["compact_regions"]
                    if r["kind"] == "passage" and "rating rationale" in r["text"])
        atom = self.atom("A", "rating", "rating.current_rating", "Facility is rated A+.", [page],
                         accounting_scope="consolidated")
        path, _ = self.save_bundle("A", [atom])
        claims, packet = sections.load_section_bundles(
            [path], self.ctx, self.ext, self.sidecar, self.requests
        )
        self.assertFalse(packet["candidates"])
        self.assertEqual(claims[0]["evidence_decision"], "WITHHELD")
        self.assertIn("Accounting scope", claims[0]["validation"]["reason"])

    def test_section_generation_requires_exact_authorization_and_sends_once(self):
        request = self.requests["A"]
        payload = credit_fresh.payload_for(request)
        digest = provider.sha_bytes(provider.wire_bytes(payload))
        prepared = self.root/"live"; prepared.mkdir()
        (prepared/"payload-A.json").write_bytes(provider.wire_bytes(payload))
        freeze = {"assignments": {"A": {"payload_sha256": digest, "budget": {"reservation_usd": "0.05"}}}}
        raw = json.dumps({"assignment_id": "A", "disclosures": []}).encode()
        verified = (freeze, self.ctx, self.ext, self.sidecar, {"A": request})
        with patch.object(credit_fresh, "verify_sections", return_value=verified), \
             patch("scrapper.research.general_ai_experiment.runner.api_key", return_value="synthetic-key"), \
             patch("scrapper.research.general_ai_experiment.runner.request", return_value=raw) as transport:
            with self.assertRaises(ValueError):
                credit_fresh.generate_section(prepared, "A", "wrong", "0.05")
            transport.assert_not_called()
            credit_fresh.generate_section(prepared, "A", digest, "0.05")
            self.assertEqual(transport.call_count, 1)
            with self.assertRaises(FileExistsError):
                credit_fresh.generate_section(prepared, "A", digest, "0.05")
            self.assertEqual(transport.call_count, 1)


if __name__ == "__main__":
    unittest.main()
