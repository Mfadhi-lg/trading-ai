from copy import deepcopy
import unittest

from scrapper.research.document_intelligence.credit_candidate_provider import validate_atom
from scrapper.research.document_intelligence.credit_candidate_repair import (
    classification_family, explicit_accounting_scope, propose_correction,
)
from scrapper.research.document_intelligence.credit_sections import (
    ASSIGNMENTS, build_section_material, build_section_request,
)
from scrapper.research.document_intelligence.credit_candidate_provider import build_region_catalog, FULL_VERSION
from scrapper.research.document_intelligence.evidence import canonical_hash
from scrapper.research.document_intelligence.schema import DocumentContext


class Phase7ContractTests(unittest.TestCase):
    def atom(self, **changes):
        atom = {
            "id": "x", "disclosure_category": "assessment",
            "field": "assessment.revenue_and_profitability",
            "statement": "Revenue was Rs. 20 crore in FY2026.", "value": "20", "unit": "Rs. crore",
            "period": "FY2026", "period_kind": "historical_period", "accounting_scope": None,
            "attribution": "Agency", "statement_kind": "historical_fact", "conditions": [],
            "qualifications": [], "support_region_ids": ["S001-000000-000010"],
        }
        atom.update(changes)
        return atom

    def region(self, *, heading="Rationale", text="Revenue was Rs. 20 crore in FY2026."):
        return {"id": "S001-000000-000010", "kind": "passage", "heading": heading,
                "text": text, "evidence": [{"page": 1, "kind": "page_text", "element": None,
                    "start": 0, "end": len(text), "quote": text, "text_origin": "native", "bbox": None}]}

    def test_period_and_null_combinations(self):
        validate_atom(self.atom())
        validate_atom(self.atom(statement="Agency expects growth.", value=None, unit=None, period=None,
                                period_kind="expectation_horizon", statement_kind="expectation"))
        validate_atom(self.atom(statement="Current business strength.", value=None, unit=None, period=None,
                                period_kind="unknown", statement_kind="assessment"))
        for changes in (
            {"period": None},
            {"period": None, "period_kind": "as_of"},
            {"period": "FY2026", "period_kind": "unknown"},
            {"period": None, "period_kind": "expectation_horizon", "statement_kind": "assessment"},
            {"period": "FY2027", "period_kind": "historical_period", "statement_kind": "expectation"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_atom(self.atom(**changes))

    def test_scope_requires_applicable_heading_or_table_header(self):
        rating = self.atom(field="rating.current_rating", disclosure_category="rating", accounting_scope="consolidated")
        self.assertIsNone(explicit_accounting_scope(rating, [self.region(text="Rating [ICRA]A+ (Stable).")]))
        financial = self.atom(accounting_scope="consolidated")
        scoped = self.region(heading="Key financial indicators (audited)",
                             text="Company name (consolidated) FY2025 FY2026\nOperating income 10 20")
        self.assertEqual(explicit_accounting_scope(financial, [scoped]), "consolidated")
        unscoped = self.region(text="The group also has consolidated subsidiaries elsewhere.\nRevenue was 20.")
        self.assertIsNone(explicit_accounting_scope(financial, [unscoped]))

    def test_capex_classification_and_actual_plan_separation(self):
        self.assertEqual(classification_family("assessment.capex_and_execution_risks"), "capex")
        original = self.atom(id="capex", field="assessment.debt_and_leverage",
            statement="AIL incurred capex of Rs. 384 crore in FY2026, while another Rs. 300 crore is lined up.")
        proposal = propose_correction(original, [self.region(text=original["statement"])], assignment_id="C",
            response_sha256="b" * 64, json_pointer="/disclosures/1", parent_claim_id="p")
        self.assertEqual(proposal["fields"], ["assessment.capex_and_execution_risks"])
        self.assertEqual(proposal["statement"], "AIL incurred capex of Rs. 384 crore in FY2026.")
        self.assertIn("field", proposal["transformation_provenance"]["changes"])

    def test_unsupported_scope_repair_is_new_unadmitted_identity(self):
        original = self.atom(field="rating.current_rating", disclosure_category="rating",
                             statement="Facility is rated [ICRA]A+.", accounting_scope="consolidated")
        proposal = propose_correction(original, [self.region(text=original["statement"])], assignment_id="A",
            response_sha256="c" * 64, json_pointer="/disclosures/0", parent_claim_id="p")
        self.assertIsNone(proposal["accounting_scope"])
        self.assertTrue(proposal["atomic_key"].startswith("correction:v1:"))
        self.assertFalse(proposal["transformation_provenance"]["automatic_admission"])
        self.assertEqual(proposal["parent_candidate_sha256"], canonical_hash(original))

    def test_heading_routing_contextual_overlaps_and_full_accounting(self):
        ctx = DocumentContext(document_id="routing", company_name="Example", content_sha256="a" * 64,
                              document_type="credit_rating", report_date="2026-05-01")
        text = ("Rationale\nStable outlook reflects expected earnings.\n"
                "Analytical approach\nParent support is not applicable; accounts are consolidated.\n"
                "About the company\nThe issuer operates three segments with common-site synergies.\n"
                "Rating history for past three years\nRating remained on watch in FY2025.\n")
        extraction = {"extraction_status": "ok", "parser_name": "synthetic", "parser_version": "1", "pages": [
            {"page_or_slide_number": 1, "selected_text": text, "selected_text_origin": "native", "elements": []}
        ]}
        catalog = build_region_catalog(ctx, extraction, version=FULL_VERSION)
        sidecar, manifest = build_section_material(ctx, extraction, catalog)
        by_heading = {r["heading"]: set(r["assignments"]) for r in sidecar["compact_regions"]}
        self.assertTrue({"A", "B", "C", "D"}.issubset(by_heading["Rationale"]))
        self.assertTrue({"A", "B", "C"}.issubset(by_heading["Analytical approach"]))
        self.assertIn("B", by_heading["About the company"])
        self.assertIn("A", by_heading["Rating history for past three years"])
        self.assertEqual(manifest["coverage"]["source_text_coverage_pct"], 100.0)
        self.assertEqual(sum(len(r["text"]) for r in sidecar["compact_regions"]), len(text))
        for key in ASSIGNMENTS:
            request = build_section_request(ctx, key, sidecar)
            self.assertIn("Never invent a date", request["instructions"])

    def test_correction_is_deterministic_and_preserves_original(self):
        original = self.atom(period=None, period_kind="as_of", accounting_scope="standalone")
        frozen = deepcopy(original)
        args = dict(assignment_id="B", response_sha256="d" * 64,
                    json_pointer="/disclosures/2", parent_claim_id="p")
        first = propose_correction(original, [self.region()], **args)
        second = propose_correction(original, [self.region()], **args)
        self.assertEqual(first, second)
        self.assertEqual(original, frozen)
        self.assertEqual(first["period_kind"], "unknown")
        self.assertIsNone(first["accounting_scope"])


if __name__ == "__main__":
    unittest.main()
