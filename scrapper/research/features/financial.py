"""Fail-closed, point-in-time ML feature dataset construction."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from calendar import monthrange
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from typing import Any, Iterable

from datetime import datetime, timezone

from ...core.reporting_scope import STANDALONE_SCOPE, preferred_scope, require_scope

TRANSFORMATION_VERSION = "screener_ml_contract_v2_standalone"


class MLContractError(ValueError):
    """Raised when feature provenance is incomplete or scope is ambiguous."""


@dataclass(frozen=True)
class MLFeature:
    company_id: int
    period: str
    reporting_scope: str
    available_at: str
    source_artifact_id: int
    transformation_version: str
    feature_code: str
    value: str | None


def build_screener_ml_features(
    conn: sqlite3.Connection, *, feature_codes: Iterable[str] | None = None,
    transformation_version: str = TRANSFORMATION_VERSION,
) -> tuple[MLFeature, ...]:
    """Build only accepted standalone, point-in-time observations.

    A missing period, timestamp, blob, or a scope mixed within the selected
    company-period is a data-quality error.  It is intentionally not imputed.
    """
    if transformation_version != TRANSFORMATION_VERSION:
        raise MLContractError(
            f"incompatible transformation version: {transformation_version!r}"
        )
    wanted = tuple(feature_codes or ())
    clause = ""
    parameters: tuple[object, ...] = ()
    if wanted:
        clause = " AND metric.metric_code IN (%s)" % ",".join("?" for _ in wanted)
        parameters = wanted
    # Fail before selecting data if an allegedly standalone accepted page has a
    # mixed or unresolved financial observation.  Filtering it out would hide
    # a provenance breach from the model-building caller.
    invalid = conn.execute(
        """SELECT page.company_id, page.resolved_source_period, page.parse_id,
                  financial.reporting_scope
             FROM accepted_page_versions AS page
             JOIN financial_observations AS financial ON financial.parse_id=page.parse_id
            WHERE page.source='screener' AND page.reporting_scope='standalone'
              AND (financial.reporting_scope <> 'standalone'
                   OR financial.available_at_utc IS NULL
                   OR trim(financial.available_at_utc)='')
            LIMIT 1"""
    ).fetchone()
    if invalid is not None:
        raise MLContractError(
            "invalid financial observation at ML boundary "
            f"company_id={invalid['company_id']} period={invalid['resolved_source_period']} "
            f"parse_id={invalid['parse_id']} scope={invalid['reporting_scope']}"
        )
    rows = conn.execute(
        """SELECT page.company_id, page.resolved_source_period AS period,
                  page.reporting_scope, page.parse_id, artifact.blob_id,
                  metric.metric_code, metric.value_decimal, metric.available_at_utc
             FROM accepted_page_versions AS page
             JOIN parse_artifacts AS artifact ON artifact.parse_id=page.parse_id
             JOIN company_metric_observations AS metric ON metric.parse_id=page.parse_id
            WHERE page.source='screener'
              AND page.reporting_scope='standalone'
              AND page.resolved_source_period NOT IN ('', 'not_applicable')""" + clause +
        " ORDER BY page.company_id, page.resolved_source_period, page.reporting_scope, metric.metric_code",
        parameters,
    ).fetchall()
    grouped: dict[tuple[int, str], list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault((int(row["company_id"]), str(row["period"])), []).append(row)
    features: list[MLFeature] = []
    for (company_id, period), observations in grouped.items():
        scopes = {str(row["reporting_scope"]) for row in observations}
        selected_scope = preferred_scope(scopes)
        selected = [row for row in observations if row["reporting_scope"] == selected_scope]
        # Each metric must have exactly one accepted value in the selected view.
        seen: set[str] = set()
        for row in selected:
            code = row["metric_code"]
            if not code or code in seen or not row["available_at_utc"] or row["blob_id"] is None:
                raise MLContractError(
                    f"unresolved ML observation company_id={company_id} period={period} scope={selected_scope}"
                )
            try:
                available_at = datetime.fromisoformat(str(row["available_at_utc"]).replace("Z", "+00:00"))
            except ValueError as exc:
                raise MLContractError("ML observation has invalid available_at") from exc
            if available_at.tzinfo is None or available_at.astimezone(timezone.utc) > datetime.now(timezone.utc):
                raise MLContractError("ML observation has invalid point-in-time availability")
            seen.add(str(code))
            features.append(MLFeature(
                company_id, period, selected_scope, str(row["available_at_utc"]),
                int(row["blob_id"]), transformation_version, str(code), row["value_decimal"],
            ))
    return tuple(features)

# B1: statement observations, separate from the legacy snapshot metric export.
COMPANY_QUARTER_VERSION = 'v1'


@dataclass(frozen=True)
class QuarterFeature:
    feature_code: str
    value: str | None
    status: str
    unit_code: str
    scale_multiplier: str
    period_end: str | None
    period_type: str
    available_at_utc: str | None
    inputs: tuple[dict[str, Any], ...] = ()
    formula: str = 'stored_value'


@dataclass(frozen=True)
class CompanyQuarterFeatures:
    company_id: int
    period_end: str | None
    decision_time: str
    reporting_scope: str
    feature_version: str
    status: str
    completeness: str
    reasons: tuple[str, ...]
    provenance: dict[str, Any]
    features: tuple[QuarterFeature, ...]
    trends: tuple[QuarterFeature, ...] = ()
    supplemental_observations: tuple[dict[str, Any], ...] = ()


def _utc(raw: str, name: str) -> datetime:
    try:
        value = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except (TypeError, ValueError) as exc:
        raise MLContractError(f'invalid {name}') from exc
    if value.tzinfo is None:
        raise MLContractError(f'{name} must have timezone')
    value = value.astimezone(timezone.utc)
    if value > datetime.now(timezone.utc):
        raise MLContractError(f'future {name}')
    return value


def _rows(conn: sqlite3.Connection, sql: str, args=()) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, args)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _end_month(period: str, offset: int = 0) -> str:
    try:
        if len(period) not in {7, 10}:
            raise ValueError('invalid period length')
        if len(period) == 10:
            datetime.strptime(period, '%Y-%m-%d')
        date = datetime.strptime(period[:7], '%Y-%m')
        month = date.year * 12 + date.month - 1 + offset
        year, index = divmod(month, 12)
        return f'{year:04}-{index + 1:02}-{monthrange(year, index + 1)[1]:02}'
    except ValueError as exc:
        raise MLContractError(f'invalid reporting period {period!r}') from exc


def company_quarter_features(
    conn: sqlite3.Connection, company_id: int, decision_time: str, *,
    scope: str = STANDALONE_SCOPE, feature_version: str = COMPANY_QUARTER_VERSION,
    period: str | None = None, sector: str = 'unknown', source: str | None = None,
    evidence_kind: str = 'stored_records',
) -> CompanyQuarterFeatures:
    """SELECT-only interface; caller supplies a read-only connection.

    No provider is fetched or privileged. Ambiguous source versions fail closed.
    Unknown sectors suppress operating/debt formulas. Retained-export fixtures
    must use evidence_kind='retained_export' and are never certified PIT-safe.
    """
    require_scope(scope)
    if feature_version != COMPANY_QUARTER_VERSION:
        raise MLContractError('unsupported company-quarter feature version')
    if sector not in {'industrial', 'bank', 'nbfc', 'insurer', 'unknown'}:
        raise MLContractError('unsupported sector applicability')
    if evidence_kind not in {'stored_records', 'retained_export'}:
        raise MLContractError('unsupported evidence kind')
    cutoff = _utc(decision_time, 'decision_time')
    # Historical events are essential: the current pointer alone loses revisions.
    events = _rows(conn, '''SELECT company_id,source,reporting_scope,
        resolved_source_period,parse_id,accepted_at_utc,action,reason_json
        FROM acceptance_events WHERE company_id=? AND reporting_scope=?''', (company_id, scope))
    pointers = _rows(conn, '''SELECT company_id,source,reporting_scope,
        resolved_source_period,parse_id,accepted_at_utc FROM accepted_page_versions
        WHERE company_id=? AND reporting_scope=?''', (company_id, scope))
    candidates = [dict(r, history_proven='migration_003_current_pointer' not in r['reason_json']) for r in events if r['action'] == 'accepted']
    for pointer in pointers:
        if not any(all(r[k] == pointer[k] for k in ('source', 'parse_id', 'resolved_source_period')) for r in candidates):
            candidates.append(dict(pointer, history_proven=False))
    eligible = []
    for row in candidates:
        if source is not None and row['source'] != source:
            continue
        if row['resolved_source_period'] in ('', 'not_applicable', None):
            continue
        end = _end_month(row['resolved_source_period'])
        if period is not None and end != _end_month(period):
            continue
        accepted = _utc(row['accepted_at_utc'], 'accepted_at_utc')
        if end > cutoff.date().isoformat() or accepted > cutoff:
            continue
        if any(e['action'] == 'rejected' and e['parse_id'] == row['parse_id']
               and e['source'] == row['source'] and accepted <= _utc(e['accepted_at_utc'], 'rejection_time') <= cutoff
               for e in events):
            continue
        lineage = _rows(conn, '''SELECT a.parse_id,a.blob_id,a.parser_name,a.parser_version,
            a.validation_status,a.parsed_at_utc,b.content_sha256,b.first_observed_at_utc AS blob_first_observed_at_utc
            FROM parse_artifacts a LEFT JOIN response_blobs b ON b.blob_id=a.blob_id
            WHERE a.parse_id=?''', (row['parse_id'],))
        if len(lineage) != 1 or not lineage[0]['content_sha256'] or lineage[0]['blob_id'] is None:
            raise MLContractError('accepted parse lacks blob lineage')
        data = lineage[0]
        captured = _utc(data['blob_first_observed_at_utc'], 'blob first observation')
        if _utc(data['parsed_at_utc'], 'parse timestamp') > accepted:
            raise MLContractError('acceptance precedes parse')
        if captured > accepted:
            raise MLContractError('acceptance precedes blob capture')
        if captured > cutoff:
            continue
        if data['validation_status'] not in {'passed', 'passed_with_warnings'}:
            raise MLContractError('accepted parse did not pass validation')
        eligible.append(dict(row, **{k: v for k, v in data.items() if k != 'parse_id'}, period_end=end))
    if not eligible:
        return CompanyQuarterFeatures(company_id, _end_month(period) if period else None,
            cutoff.isoformat(), scope, feature_version, 'EXPLORATORY_BACKFILL',
            'INSUFFICIENT_EVIDENCE', ('NO_ACCEPTED_VERSION_AT_CUTOFF',), {}, ())
    end = max(r['period_end'] for r in eligible)
    eligible = [r for r in eligible if r['period_end'] == end]
    if len({r['source'] for r in eligible}) > 1:
        raise MLContractError('multiple accepted sources: specify source explicitly')
    latest = max(_utc(r['accepted_at_utc'], 'acceptance') for r in eligible)
    eligible = [r for r in eligible if _utc(r['accepted_at_utc'], 'acceptance') == latest]
    if len({r['parse_id'] for r in eligible}) != 1:
        raise MLContractError('ambiguous same-time accepted versions')
    chosen = eligible[0]
    pid = chosen['parse_id']
    raw = _rows(conn, 'SELECT * FROM financial_observations WHERE parse_id=?', (pid,))
    supplemental = []
    for table in ('growth_observations', 'company_metric_observations'):
        supplemental.extend(dict(r, source_table=table) for r in _rows(conn, f'SELECT * FROM {table} WHERE parse_id=?', (pid,)))
    # Validate before filtering: mixed scopes and malformed availability are errors.
    filtered = []
    deferred_availability = False
    for row in raw + supplemental:
        if row['reporting_scope'] != scope:
            raise MLContractError('mixed reporting scope in accepted parse')
        observed = _utc(row['first_observed_at_utc'], 'observation first capture')
        available = _utc(row['available_at_utc'], 'observation availability')
        publication = row.get('source_published_at_utc')
        if available < observed or (publication and available < _utc(publication, 'publication')):
            raise MLContractError('availability precedes evidence')
        if available > cutoff:
            deferred_availability = True
            continue
        filtered.append(row)
    financial = [r for r in filtered if 'financial_observation_id' in r]
    supplemental = [dict(r, role='SNAPSHOT_CONTEXT_ONLY', blob_id=chosen['blob_id'],
                         blob_sha256=chosen['content_sha256'], reference_basis=evidence_kind)
                    for r in filtered if 'source_table' in r]
    reasons = []
    if evidence_kind != 'stored_records' or not chosen['history_proven'] or any(
        r.get('availability_basis') not in {'first_observed', 'source_publication_date', 'exchange_filing_timestamp'}
        for r in filtered
    ):
        reasons.append('HISTORICAL_PROVENANCE_NOT_PROVEN')
    if sector == 'unknown':
        reasons.append('SECTOR_APPLICABILITY_UNKNOWN')
    if deferred_availability:
        reasons.append('OBSERVATIONS_AFTER_CUTOFF_EXCLUDED')
    provenance = dict(chosen, evidence_kind=evidence_kind, sector=sector)
    finance = sector in {'bank', 'nbfc'}

    def lookup(section, target, code=None, label=None, interval='quarter'):
        matches = [r for r in financial if r['section_code'] == section and r['period_end'] == target
                   and r['period_type'] == interval and
                   ((label is not None and r['line_item_raw'] == label) or (label is None and r['line_item_code'] == code))]
        if len(matches) > 1:
            raise MLContractError(f'ambiguous observation {section}/{code or label}/{target}')
        return matches[0] if matches else None

    def raw_feature(name, row, unit='INR_CRORE', status=None, target=end, interval='quarter'):
        scale = '10000000' if unit == 'INR_CRORE' else '1'
        if row is None:
            return QuarterFeature(name, None, status or 'MISSING', unit, scale, target, interval, None)
        if row['unit_code'] != unit or row['scale_multiplier'] != scale:
            raise MLContractError(f'incompatible unit/scale for {name}')
        value = row['value_decimal']
        if row.get('missing_kind') and value is not None:
            raise MLContractError('missing value carries numeric content')
        if value is not None:
            try:
                if not Decimal(value).is_finite():
                    raise InvalidOperation
            except InvalidOperation as exc:
                raise MLContractError('invalid numeric observation') from exc
        reference = dict(row, source_table='financial_observations', reference_basis=evidence_kind, blob_id=chosen['blob_id'],
                         blob_sha256=chosen['content_sha256'])
        available = max(_utc(row['available_at_utc'], 'availability'), latest,
                        _utc(chosen['blob_first_observed_at_utc'], 'capture')).isoformat()
        return QuarterFeature(name, value, 'PRESENT' if value is not None else 'MISSING', unit,
                              scale, row['period_end'], row['period_type'], available, (reference,))

    revenue = lambda target: lookup('quarters', target, label='Revenue +' if finance else None, code='sales')
    pat = lambda target: lookup('quarters', target, code='net_profit')
    features = [raw_feature('quarter_revenue', revenue(end)),
                raw_feature('quarter_pbt', lookup('quarters', end, code='profit_before_tax')),
                raw_feature('quarter_pat', pat(end)),
                raw_feature('quarter_eps', lookup('quarters', end, code='eps_inr'), 'INR')]
    # Explicit applicability; insurers and unknown sectors get no industrial proxy.
    for name, code, label, applies, unit in (
        ('quarter_operating_profit', 'operating_profit', None, sector == 'industrial', 'INR_CRORE'),
        ('quarter_operating_margin', 'operating_margin_pct', None, sector == 'industrial', 'PERCENT_POINT'),
        ('quarter_financing_profit', None, 'Financing Profit', finance, 'INR_CRORE'),
        ('quarter_financing_margin', None, 'Financing Margin %', finance, 'PERCENT_POINT')):
        features.append(raw_feature(name, lookup('quarters', end, code=code, label=label) if applies else None,
                        unit, status='NOT_APPLICABLE' if sector != 'unknown' else 'UNVERIFIED_APPLICABILITY'))
    for metric, getter in (('revenue', revenue), ('pat', pat)):
        for suffix, offset in (('qoq', -3), ('yoy', -12)):
            now = raw_feature(metric, getter(end))
            before = raw_feature(metric, getter(_end_month(end, offset)), target=_end_month(end, offset))
            value, status = None, 'MISSING'
            if now.value is not None and before.value is not None:
                if Decimal(before.value) <= 0:
                    status = 'INVALID_COMPARATOR'
                else:
                    with localcontext() as context:
                        context.prec = 50
                        value = str(((Decimal(now.value) / Decimal(before.value) - 1) * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN))
                    status = 'PRESENT'
            features.append(QuarterFeature(f'quarter_{metric}_{suffix}', value, status,
                'PERCENT_POINT', '1', end, 'quarter', max((x for x in (now.available_at_utc, before.available_at_utc) if x), default=None),
                now.inputs + before.inputs, '100 * (current / comparator - 1); comparator > 0'))
    balance = {r['period_end'] for r in financial if r['section_code'] == 'balance_sheet' and r['period_type'] == 'fiscal_year' and r['period_end'] and r['period_end'] <= end}
    cash = {r['period_end'] for r in financial if r['section_code'] == 'cash_flow' and r['period_type'] == 'fiscal_year' and r['period_end'] and r['period_end'] <= end}
    annual = max(balance & cash, default=None)
    # The parser's fiscal_year flag alone cannot validate an interim column.
    if annual and any(p > annual for p in balance):
        reasons.append('LATER_BALANCE_INTERVAL_UNVERIFIED')
    for name, section, code, label, applies in (
        ('annual_borrowings', 'balance_sheet', 'borrowings', 'Borrowing' if finance else None, sector in {'industrial', 'bank', 'nbfc'}),
        ('annual_deposits', 'balance_sheet', None, 'Deposits', sector == 'bank'),
        ('annual_cfo', 'cash_flow', 'cash_from_operating_activity', None, True),
        ('annual_fcf', 'cash_flow', 'free_cash_flow', None, True)):
        features.append(raw_feature(name, lookup(section, annual, code=code, label=label, interval='fiscal_year') if annual and applies else None,
                        status='NOT_APPLICABLE' if not applies and sector != 'unknown' else 'MISSING', target=annual, interval='fiscal_year'))
    # Raw dated quarter series only: no undocumented trend score or CAGR threshold.
    trends = []
    for target in sorted({r['period_end'] for r in financial if r['section_code'] == 'quarters' and r['period_type'] == 'quarter' and r['period_end'] and r['period_end'] <= end})[-8:]:
        for metric, getter in (('revenue', revenue), ('pat', pat)):
            trends.append(raw_feature(f'quarter_{metric}_history', getter(target), target=target))
    required = ['quarter_revenue', 'quarter_pbt', 'quarter_pat', 'quarter_eps']
    if sector == 'industrial':
        required += ['quarter_operating_profit', 'quarter_operating_margin']
    elif finance:
        required += ['quarter_financing_profit', 'quarter_financing_margin']
    completeness = 'COMPLETE' if sector not in {'unknown', 'insurer'} and all(
        f.status == 'PRESENT' for f in features if f.feature_code in required) else 'INSUFFICIENT_EVIDENCE'
    if completeness != 'COMPLETE':
        reasons.append('CORE_FEATURES_OR_SECTOR_MAPPING_INCOMPLETE')
    if any(f.status in {'MISSING', 'INVALID_COMPARATOR'} for f in features) and completeness == 'COMPLETE':
        completeness = 'PARTIAL'
    status = 'EXPLORATORY_BACKFILL' if 'HISTORICAL_PROVENANCE_NOT_PROVEN' in reasons else 'PIT_SAFE_FROM_CAPTURE'
    return CompanyQuarterFeatures(company_id, end, cutoff.isoformat(), scope, feature_version,
        status, completeness, tuple(reasons), provenance, tuple(features), tuple(trends), tuple(supplemental))
