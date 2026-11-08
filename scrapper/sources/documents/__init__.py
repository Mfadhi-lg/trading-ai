"""Offline document-link discovery adapters for Phase 2."""

from .discovery import (
    ARTIFACT_TYPES,
    DISCOVERY_SOURCES,
    DiscoveredArtifactCandidate,
    DiscoveryPage,
    DiscoveryPlan,
    build_discovery_plan,
)

__all__ = [
    "ARTIFACT_TYPES",
    "DISCOVERY_SOURCES",
    "DiscoveredArtifactCandidate",
    "DiscoveryPage",
    "DiscoveryPlan",
    "build_discovery_plan",
]
from .bse_recovery import BSECorporateRatingResolver
from .debt_recovery import DebtIssueDocumentResolver
from .issuer_ir_recovery import IssuerIRResolver
from .nse_recovery import NSECorporateAnnouncementResolver

__all__ = (
    "BSECorporateRatingResolver",
    "DebtIssueDocumentResolver",
    "IssuerIRResolver",
    "NSECorporateAnnouncementResolver",
)
