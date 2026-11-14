"""Explicit versioned subscriptions, verified through existing company identity."""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re

from experiments.historical_candle_archive_v1.identity import IdentityResolver
from scrapper.market_refresh.planner import aware


def resolve_definition(definition, identity, now):
    if definition.get('schema_version') != 1 or not definition.get('version'):
        raise ValueError('versioned live-watchlist definition required')
    ids = definition['company_ids']
    if not 20 <= len(ids) <= 50 or len(set(ids)) != len(ids):
        raise ValueError('20–50 unique explicit live company IDs required')
    if any(type(i) is not int or i <= 0 for i in ids):
        raise ValueError('positive company IDs required')
    observed = aware(identity['observed_at'])
    if not timedelta(0) <= aware(now)-observed <= timedelta(days=1):
        raise ValueError('current provider identity snapshot required (maximum 24h)')
    if not identity.get('source_ref'):
        raise ValueError('identity source evidence required')
    resolver = IdentityResolver(identity['securities'], identity['identifiers'],
                                identity.get('scopes',[]), identity['provider_records'])
    resolved = {}
    for company_id in ids:
        row = resolver.resolve(company_id)
        key = row.get('instrument_key','')
        if (row.get('status') != 'EXACT_EXISTING_IDENTIFIER_AND_ISIN_OVERLAY'
            or row.get('conflicts') or not re.fullmatch(r'NSE_EQ\|INE[A-Z0-9]{9}',key)):
            raise ValueError('unverified or conflicting current NSE identity')
        if key in resolved: raise ValueError('duplicate underlying instrument')
        resolved[key] = dict(row, company_id=company_id, identity_ref=identity['source_ref'])
    return resolved


# Cache only public identity overlays and exact resolved watchlists. Never tokens.
# Pinned byte digest + filesystem identity + IST day controls invalidation.
_CACHE={}


def load(config, now, stop_event=None):
    from zoneinfo import ZoneInfo
    path=Path(config['identity_path'])
    stat=path.stat()
    day=aware(now).astimezone(ZoneInfo('Asia/Kolkata')).date().isoformat()
    signature=(str(path.resolve()),config['identity_sha256'],stat.st_mtime_ns,stat.st_size,
               day,str(config.get('state_dir')),config.get('identity_refresh_enabled',False))
    definition_raw=Path(config['watchlist_path']).read_bytes()
    definition=json.loads(definition_raw)
    cached=_CACHE.get(signature)
    if cached is None:
        identity_raw=path.read_bytes()
        if hashlib.sha256(identity_raw).hexdigest()!=config['identity_sha256']:
            raise ValueError('identity snapshot hash mismatch')
        identity=json.loads(identity_raw)
        if config.get('identity_refresh_enabled'):
            if stop_event is None:raise ValueError('live stop/admission context required')
            from .identity_refresh import current
            identity=current(config,identity,now,stop_event)
        if len(_CACHE)>=16:_CACHE.clear()
        cached=dict(identity=identity,definitions={});_CACHE[signature]=cached
    identity=cached['identity']
    if not timedelta(0)<=aware(now)-aware(identity['observed_at'])<=timedelta(days=1):
        raise ValueError('current provider identity snapshot required (maximum 24h)')
    ident=hashlib.sha256(definition_raw).hexdigest()
    if ident not in cached['definitions']:
        if len(cached['definitions'])>=100:raise ValueError('bounded daily watchlist changes exhausted')
        cached['definitions'][ident]=resolve_definition(definition,identity,now)
    return definition,cached['definitions'][ident]
