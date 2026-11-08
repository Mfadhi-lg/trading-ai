"""C20.1 proposed shared request policy. No I/O or executor integration.

Existing reviewed restrictions remain a floor. Limits here are conservative
coordination defaults, not a new publisher permission or operator authorization.
"""
from dataclasses import dataclass
from urllib.parse import urlsplit
from .access_policy import is_blocked_terms_target

VERSION = 'c20.1-v1'
# Cross-domain aliases are explicit, never inferred from registrable domains.
GROUPS = {
    'care': ('careratings.com',),
    'india-ratings': ('indiaratings.co.in',),
    'acuite': ('acuite.in',),
    'brickwork': ('bcrisp.in', 'brickworkratings.com'),
    'infomerics': ('infomerics.com',),
    'crisil': ('crisil.com', 'crisilratings.com'),
    'icra': ('icra.in',),
    'screener': ('screener.in',),
}
MODES = frozenset({'generic', 'rating', 'browser', 'screener'})

@dataclass(frozen=True)
class OperatorAuthorization:
    # Trusted caller supplies existing reviewed finite-run authorization evidence.
    # This value is a scope receipt, not an authorization-issuing API.
    run_id: str
    reference: str
    hosts: tuple[str, ...]
    expires_at: float
    restricted_hosts: tuple[str, ...] = ()

    def covers(self, host, now):
        return bool(self.run_id.strip() and self.reference.strip()) and now < self.expires_at and host in self.hosts


@dataclass(frozen=True)
class HostPolicy:
    host: str
    group: str
    max_concurrency: int = 1
    min_delay_seconds: float = 4.0
    blocked: bool = False
    version: str = VERSION


def canonical_host(target):
    value = target.strip().lower().rstrip('.')
    parsed = urlsplit(value if '://' in value else 'https://' + value)
    if parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password:
        raise ValueError('Expected an HTTP(S) target without credentials')
    host = (parsed.hostname or '').rstrip('.').encode('idna').decode('ascii')
    if not host or any(c.isspace() for c in host) or '%' in host or not all(
            c.isalnum() or c in '.-:' for c in host):
        raise ValueError('Invalid target host')
    _ = parsed.port  # Validate malformed ports; ports never split a permit group.
    return host


def policy_for(target):
    host = canonical_host(target)
    group = next((name for name, aliases in GROUPS.items() if any(
        host == a or host.endswith('.' + a) for a in aliases)), 'host:' + host)
    blocked = is_blocked_terms_target(host)
    return HostPolicy(host, group, 0 if blocked else 1, 4.0, blocked)
