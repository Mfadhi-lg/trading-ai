"""Offline, source-evidenced research signals; no acquisition or DB writes."""

from .extract import extract_intelligence
from .schema import DocumentContext, ResearchEvidence
from .temporal import compare_guidance, eligible_at

__all__ = ["DocumentContext", "ResearchEvidence", "extract_intelligence", "eligible_at", "compare_guidance"]
