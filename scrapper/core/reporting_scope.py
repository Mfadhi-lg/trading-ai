"""The company-period reporting-scope contract.

Financial research and ML use *only* the actual standalone statement.  A
consolidated filing can be retained as source provenance, but is never a
financial observation, a queue target, or an ML fallback.
"""
from __future__ import annotations

STANDALONE_SCOPE = "standalone"
PREFERRED_SCOPE = STANDALONE_SCOPE
# Kept for parsing raw exchange provenance.  ``require_scope`` is deliberately
# stricter: it is used at financial-data boundaries.
VALID_SCOPES = frozenset(("consolidated", STANDALONE_SCOPE))


def require_scope(scope: str) -> str:
    if scope != STANDALONE_SCOPE:
        raise ValueError("financial reporting_scope must be standalone")
    return STANDALONE_SCOPE


def preferred_scope(scopes: set[str] | tuple[str, ...] | list[str]) -> str:
    """Pick a view without discarding the supplied scope records."""
    values = set(scopes)
    if values != {STANDALONE_SCOPE}:
        raise ValueError("ML data requires exactly one standalone reporting scope")
    return STANDALONE_SCOPE


def canonical_company_path(path: str) -> str:
    """A Screener URL identity with its reporting view removed."""
    normalized = "/" + path.strip("/") + "/"
    return normalized.removesuffix("/consolidated/") + "/" if normalized.endswith("/consolidated/") else normalized
