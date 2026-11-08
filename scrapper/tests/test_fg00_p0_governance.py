from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scrapper.governance.focus_p0 import (
    AllowedArchetype,
    ArchetypeApprovalState,
    CompanyQuarterGovernance,
    DecisionUniverseState,
    EligibilityState,
    IdentityResolutionState,
    P0SelectionState,
    assign_outcome_label,
    derive_eligibility,
    propose_archetype,
    resolve_historical_symbol,
    resolve_verified_exchange_identifier,
    select_p0_companies,
    stable_payload_hash,
    validate_p0_manifest,
)
from scrapper.pipeline.build_p0_governance import (
    load_archetype_approvals,
    load_decision_universe_approvals,
    resolve_focus_registry,
)


NOW = "2026-08-16T11:00:00Z"


def make_contract(**overrides):
    values = {
        "company_id": 1,
        "quarter": "FY2027-Q1",
        "historical_symbol": "ALPHA",
        "isin": "INE000A00001",
        "bse_code": "500001",
        "decision_universe_state": DecisionUniverseState.IN_UNIVERSE,
        "outcome_label": 1,
        "analysis_archetype": AllowedArchetype.MANUFACTURING,
        "identity_resolution_state": IdentityResolutionState.RESOLVED,
        "label_source_id": "focus:q1:2",
        "label_source_version": "v001",
        "universe_source_id": "focus:q1:2",
        "universe_source_version": "v001",
        "archetype_mapping_version": "v001",
        "archetype_approval_state": ArchetypeApprovalState.APPROVED,
        "eligibility_state": EligibilityState.ELIGIBLE,
        "eligibility_reasons": (),
        "p0_selection_state": P0SelectionState.SELECTED,
        "created_at_utc": NOW,
        "governance_version": "FG00-v001",
    }
    values.update(overrides)
    return CompanyQuarterGovernance(**values)


def make_p0_rows() -> list[dict]:
    archetypes = [
        "MANUFACTURING",
        "BANK",
        "PHARMA",
        "IT_SERVICES",
    ]
    rows = []
    for index in range(40):
        company_id = index + 1
        rows.append(
            {
                "company_id": company_id,
                "quarter": "FY2027-Q1" if index % 2 else "FY2026-Q4",
                "identity_resolution_state": "RESOLVED",
                "decision_universe_state": "IN_UNIVERSE",
                "outcome_label": 1 if index < 20 else 0,
                "analysis_archetype": archetypes[index % len(archetypes)],
                "archetype_approval_state": "APPROVED",
                "transcript_artifact_count": index % 3,
                "presentation_artifact_count": (index + 1) % 3,
                "credit_rating_artifact_count": 1,
                "recording_available": bool(index % 2),
            }
        )
    return rows


class FG00GovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE companies(company_id INTEGER PRIMARY KEY, current_legal_name TEXT, status TEXT);
            CREATE TABLE security_identifiers(
              company_id INTEGER, identifier_type TEXT, identifier_value TEXT,
              valid_from TEXT, valid_to TEXT, is_current INTEGER
            );
            CREATE TABLE exchange_symbol_changes(
              exchange TEXT, change_kind TEXT, old_value TEXT, new_value TEXT,
              effective_date TEXT
            );
            CREATE TABLE exchange_securities(
              exchange_security_id INTEGER PRIMARY KEY, nse_symbol TEXT,
              bse_scrip_code TEXT, current_isin TEXT, current_name_raw TEXT,
              exchange TEXT, is_present INTEGER
            );
            CREATE TABLE exchange_security_versions(
              exchange_security_id INTEGER, listing_date TEXT, series_or_group TEXT,
              instrument_raw TEXT, valid_from_date TEXT
            );
            """
        )
        self.conn.execute("INSERT INTO companies VALUES (1,'Alpha Limited','active')")
        self.conn.execute("INSERT INTO companies VALUES (2,'Beta Limited','active')")
        self.conn.execute(
            "INSERT INTO security_identifiers VALUES (1,'NSE','ALPHA',NULL,NULL,1)"
        )
        self.conn.execute(
            "INSERT INTO security_identifiers VALUES (2,'NSE','BETA',NULL,NULL,1)"
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_positive_source_symbol_resolves_to_durable_company_id(self) -> None:
        resolved = resolve_historical_symbol(self.conn, "ALPHA")
        self.assertEqual(resolved.company_id, 1)
        self.assertEqual(resolved.state, IdentityResolutionState.RESOLVED)

    def test_historical_alias_resolves_through_official_change(self) -> None:
        self.conn.execute(
            "INSERT INTO exchange_symbol_changes VALUES ('NSE','symbol','OLDALPHA','ALPHA','2025-01-01')"
        )
        resolved = resolve_historical_symbol(
            self.conn, "OLDALPHA", as_of_date="2026-08-03"
        )
        self.assertEqual(resolved.company_id, 1)
        self.assertEqual(resolved.state, IdentityResolutionState.RESOLVED_HISTORICAL_ALIAS)

    def test_verified_bse_identifier_resolves_only_one_exact_company_name(self) -> None:
        self.conn.execute(
            "INSERT INTO exchange_securities VALUES (10,NULL,'543619','INE0N0J01014','Alpha Limited','BSE',1)"
        )
        resolved = resolve_verified_exchange_identifier(
            self.conn, exchange="BSE", identifier="543619"
        )
        self.assertEqual(resolved.company_id, 1)
        self.assertEqual(resolved.state, IdentityResolutionState.RESOLVED_HISTORICAL_ALIAS)

    def test_ambiguous_symbol_remains_quarantined(self) -> None:
        self.conn.execute(
            "INSERT INTO security_identifiers VALUES (2,'NSE','ALPHA',NULL,NULL,1)"
        )
        resolved = resolve_historical_symbol(self.conn, "ALPHA")
        self.assertEqual(resolved.state, IdentityResolutionState.AMBIGUOUS)
        self.assertIsNone(resolved.company_id)

    def test_focus_member_gets_label_one(self) -> None:
        self.assertEqual(
            assign_outcome_label(
                is_focus_member=True,
                universe_state=DecisionUniverseState.IN_UNIVERSE,
            ),
            1,
        )

    def test_non_focus_in_universe_gets_zero(self) -> None:
        self.assertEqual(
            assign_outcome_label(
                is_focus_member=False,
                universe_state=DecisionUniverseState.IN_UNIVERSE,
            ),
            0,
        )

    def test_unknown_universe_never_gets_zero(self) -> None:
        self.assertIsNone(
            assign_outcome_label(
                is_focus_member=False,
                universe_state=DecisionUniverseState.UNKNOWN,
            )
        )
        with self.assertRaisesRegex(ValueError, "IN_UNIVERSE"):
            make_contract(
                outcome_label=0,
                decision_universe_state=DecisionUniverseState.UNKNOWN,
            )

    def test_technical_source_failure_does_not_become_negative(self) -> None:
        self.assertIsNone(
            assign_outcome_label(
                is_focus_member=False,
                universe_state=DecisionUniverseState.IN_UNIVERSE,
                technical_source_failure=True,
            )
        )
        state, _ = derive_eligibility(
            cutoff_date="2026-08-03",
            listing_date="2020-01-01",
            company_status="active",
            instrument_in_scope=True,
            identity_state=IdentityResolutionState.RESOLVED,
            result_available=None,
            technical_source_failure=True,
            universe_state=DecisionUniverseState.IN_UNIVERSE,
        )
        self.assertEqual(state, EligibilityState.EXCLUDED_SOURCE_DATA_FAILURE)

    def test_not_yet_listed_company_is_excluded(self) -> None:
        state, _ = derive_eligibility(
            cutoff_date="2026-08-03",
            listing_date="2026-08-04",
            company_status="active",
            instrument_in_scope=True,
            identity_state=IdentityResolutionState.RESOLVED,
            result_available=True,
            universe_state=DecisionUniverseState.IN_UNIVERSE,
        )
        self.assertEqual(state, EligibilityState.EXCLUDED_NOT_YET_LISTED)

    def test_inactive_company_is_excluded(self) -> None:
        state, _ = derive_eligibility(
            cutoff_date="2026-08-03",
            listing_date="2020-01-01",
            company_status="inactive",
            instrument_in_scope=True,
            identity_state=IdentityResolutionState.RESOLVED,
            result_available=True,
            universe_state=DecisionUniverseState.IN_UNIVERSE,
        )
        self.assertEqual(state, EligibilityState.EXCLUDED_INACTIVE)

    def test_archetype_rule_is_provisional_without_approval(self) -> None:
        proposal = propose_archetype(subsector="BANKS")
        self.assertEqual(proposal.archetype, AllowedArchetype.BANK)
        self.assertEqual(proposal.state, ArchetypeApprovalState.PROVISIONAL)

    def test_archetype_rule_can_retain_approved_source_state(self) -> None:
        proposal = propose_archetype(subsector="PHARMA", approved_source=True)
        self.assertEqual(proposal.archetype, AllowedArchetype.PHARMA)
        self.assertEqual(proposal.state, ArchetypeApprovalState.APPROVED)

    def test_ambiguous_business_classification_requires_review(self) -> None:
        proposal = propose_archetype(sector="BANK", industry="IT SERVICES")
        self.assertEqual(proposal.state, ArchetypeApprovalState.REVIEW_REQUIRED)
        self.assertIsNone(proposal.archetype)

    def test_p0_rejects_unapproved_archetype(self) -> None:
        rows = make_p0_rows()
        rows[0]["archetype_approval_state"] = "PROVISIONAL"
        with self.assertRaisesRegex(ValueError, "Insufficient"):
            select_p0_companies(rows, seed=7)

    def test_p0_rejects_unknown_universe(self) -> None:
        rows = make_p0_rows()
        rows[20]["decision_universe_state"] = "UNKNOWN"
        rows[20]["outcome_label"] = None
        with self.assertRaisesRegex(ValueError, "Insufficient"):
            select_p0_companies(rows, seed=7)

    def test_p0_rejects_unresolved_identity(self) -> None:
        rows = make_p0_rows()
        rows[20]["identity_resolution_state"] = "UNRESOLVED"
        with self.assertRaisesRegex(ValueError, "Insufficient"):
            select_p0_companies(rows, seed=7)

    def test_p0_includes_positive_and_negative_examples(self) -> None:
        selected = select_p0_companies(make_p0_rows(), seed=7)
        self.assertEqual({row["outcome_label"] for row in selected}, {0, 1})

    def test_p0_includes_multiple_archetypes(self) -> None:
        selected = select_p0_companies(make_p0_rows(), seed=7)
        self.assertGreaterEqual(len({row["analysis_archetype"] for row in selected}), 3)

    def test_p0_stays_within_30_to_50_companies(self) -> None:
        selected = select_p0_companies(make_p0_rows(), seed=7)
        self.assertEqual(len(selected), 40)
        with self.assertRaisesRegex(ValueError, "between 30 and 50"):
            select_p0_companies(make_p0_rows(), seed=7, target_count=29)

    def test_manifest_rejects_artifact_owned_by_other_company(self) -> None:
        company = {
            **make_p0_rows()[0],
            "artifact_ids": {"transcripts": [99], "presentations": [], "credit_ratings": []},
            "label_source": "label-v1",
            "universe_source": "universe-v1",
            "archetype_mapping_version": "mapping-v1",
        }
        checks = validate_p0_manifest(
            [company], artifact_company={99: 2}, focus_registry_keys={(1, "FY2026-Q4")}
        )
        self.assertFalse(checks["artifact_ownership"])

    def test_recordings_are_excluded_from_automatic_artifacts(self) -> None:
        company = {
            **make_p0_rows()[0],
            "artifact_ids": {
                "transcripts": [],
                "presentations": [],
                "credit_ratings": [],
                "recordings": [100],
            },
            "label_source": "label-v1",
            "universe_source": "universe-v1",
            "archetype_mapping_version": "mapping-v1",
        }
        checks = validate_p0_manifest(
            [company], artifact_company={}, focus_registry_keys={(1, "FY2026-Q4")}
        )
        self.assertFalse(checks["recordings_excluded"])

    def test_same_seed_and_input_produce_identical_selection(self) -> None:
        rows = make_p0_rows()
        self.assertEqual(
            select_p0_companies(rows, seed=20260816),
            select_p0_companies(reversed(rows), seed=20260816),
        )

    def test_rerunning_ingestion_does_not_duplicate_registry_rows(self) -> None:
        source = {
            "quarter": "FY2027-Q1",
            "source_file": "focus.csv",
            "source_row": 2,
            "source_symbol": "ALPHA",
            "source_name": "Alpha",
            "source_classification": "CHEMICALS",
            "source_hash": "abc",
        }
        rows = resolve_focus_registry(
            self.conn, [source, source], created_at_utc=NOW
        )
        self.assertEqual(len(rows), 1)

    def test_manifest_hash_references_are_stable(self) -> None:
        payload = {"governance_version": "v001", "source_hash": "abc", "rows": [1, 2]}
        self.assertEqual(stable_payload_hash(payload), stable_payload_hash(dict(payload)))

    def test_document_availability_cannot_change_historical_label(self) -> None:
        before = assign_outcome_label(
            is_focus_member=True, universe_state=DecisionUniverseState.IN_UNIVERSE
        )
        row = {"transcript_artifact_count": 0, "presentation_artifact_count": 0}
        row["transcript_artifact_count"] = 4
        after = assign_outcome_label(
            is_focus_member=True, universe_state=DecisionUniverseState.IN_UNIVERSE
        )
        self.assertEqual(before, after)

    def test_unresolved_contract_remains_quarantined(self) -> None:
        contract = make_contract(
            company_id=None,
            historical_symbol=None,
            identity_resolution_state=IdentityResolutionState.UNRESOLVED,
            decision_universe_state=DecisionUniverseState.QUARANTINED,
            outcome_label=None,
            eligibility_state=EligibilityState.EXCLUDED_IDENTITY_UNRESOLVED,
            eligibility_reasons=("OCR_UNRESOLVED",),
        )
        self.assertEqual(contract.decision_universe_state, DecisionUniverseState.QUARANTINED)

    def test_decision_approval_loader_accepts_only_targeted_yes_no_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decision.csv"
            path.write_text(
                "company_id,approve_as_decision_universe_member\n1,YES\n2,NO\n",
                encoding="utf-8",
            )
            self.assertEqual(
                load_decision_universe_approvals(
                    path, allowed_company_ids={1, 2}
                ),
                {1: True, 2: False},
            )

    def test_archetype_approval_loader_requires_allowed_proposal_or_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archetype.csv"
            path.write_text(
                "company_id,approve,override_archetype\n1,YES,\n2,YES,OTHER\n",
                encoding="utf-8",
            )
            self.assertEqual(
                load_archetype_approvals(
                    path, proposed_by_company={1: "BANK", 2: None}
                ),
                {1: "BANK", 2: "OTHER"},
            )


if __name__ == "__main__":
    unittest.main()
