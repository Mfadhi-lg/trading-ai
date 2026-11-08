"""Explicit opt-in for historical audits requiring retained external source bytes.

These are provenance audits, not portable software fixtures. When requested they
run normally: missing or corrupt sources fail rather than silently skipping.
Portable software contracts use synthetic sources in the default suite.
"""
import os
import unittest

_ENABLED = os.environ.get("TRADING_AI_RUN_RETAINED_AUDITS", "0")
if _ENABLED not in {"0", "1"}:
    raise ValueError("TRADING_AI_RUN_RETAINED_AUDITS must be 0 or 1")

retained_source_audit = unittest.skipUnless(
    _ENABLED == "1",
    "Retained-source audit: set TRADING_AI_RUN_RETAINED_AUDITS=1 with the original bundle; portable synthetic contract tests run by default",
)
