"""Fail-closed policy helpers for automated outbound document requests."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit


BLOCKED_TERMS = "BLOCKED_TERMS"
PUBLISHER_TERMS_RESULT = "PUBLISHER_TERMS_BLOCK_AUTOMATION"
PUBLISHER_TERMS_ERROR = (
    "Automated acquisition disabled by reviewed publisher terms"
)

# This baseline is intentionally present at the HTTP boundary as well as in the
# auditable database table.  Database checks protect queue orchestration; this
# list is the last defense for direct D02/resolver calls and policy races.
BLOCKED_TERMS_HOSTS = frozenset(
    {"crisil.com", "crisilratings.com", "icra.in"}
)


def normalize_host(value: str) -> str:
    """Normalize a hostname or an absolute URL to a comparable hostname."""

    candidate = value.strip().lower().rstrip(".")
    if "://" in candidate:
        return (urlsplit(candidate).hostname or "").lower().rstrip(".")
    return candidate


def host_matches_policy(host: str, policy_hosts: Iterable[str]) -> bool:
    """Match an exact policy host or one of its subdomains."""

    normalized = normalize_host(host)
    return bool(normalized) and any(
        normalized == policy_host or normalized.endswith("." + policy_host)
        for raw_host in policy_hosts
        if (policy_host := normalize_host(raw_host))
    )


def is_blocked_terms_target(
    url_or_host: str,
    *,
    additional_hosts: Iterable[str] = (),
) -> bool:
    """Return whether the reviewed terms prohibit automated acquisition."""

    return host_matches_policy(
        url_or_host,
        (*BLOCKED_TERMS_HOSTS, *tuple(additional_hosts)),
    )


class BlockedTermsError(ValueError):
    """Raised before HTTP when reviewed publisher terms prohibit automation."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.host = normalize_host(url)
        super().__init__(f"{PUBLISHER_TERMS_RESULT}: {self.host or url}")


def refuse_blocked_terms_target(
    url: str,
    *,
    additional_hosts: Iterable[str] = (),
) -> None:
    """Raise before an HTTP call when the target is blocked by reviewed terms."""

    if is_blocked_terms_target(url, additional_hosts=additional_hosts):
        raise BlockedTermsError(url)
