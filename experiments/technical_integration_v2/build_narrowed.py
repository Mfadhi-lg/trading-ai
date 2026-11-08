"""Offline supported global integration; never acquire or restart broad builds."""
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import csv
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from .prepare_narrowed import ROOT, OUT, BASE, sha
from .execute_narrowed import APPROVED, save, strict_payload
from .narrowed_validation import IST, NY, validate, latest_completed, yield_delta_bp, spread_bp, checkpoint

CHARTS = BASE / 'charts'
LABELS = dict(USDINR='USD/INR · Upstox USD INR', BRENT='Brent · Upstox Oil (Brent)',
    WTI='WTI · Upstox Oil (WTI)', US_TECH100='US Tech 100 · Upstox provider series',
    DJIA='Dow Jones · Upstox DOW JONES', GIFT_NIFTY='GIFT Nifty · generic Upstox quotation',
    UST2Y='US Treasury 2Y nominal par yield', UST10Y='US Treasury 10Y nominal par yield',
    UST30Y='US Treasury 30Y nominal par yield', UST10Y2Y='US Treasury 10Y minus 2Y',
    NASDAQCOM='Nasdaq Composite · exact benchmark', ES='CME E-mini S&P 500 futures · ES',
    NQ='CME E-mini Nasdaq-100 futures · NQ')
PHASES = ('PREOPEN', 'POSTCLOSE')
INJECTION = '<script src="../global-market-data.js"></script><script src="../global-context.js"></script>'
INDEX_LINK = '<p id="global-market-entry"><a href="global-market.html">Global market block · recovered history, Treasury yields and checkpoint evidence</a></p>'


def read(path):
    with path.open(newline='') as f:
        return list(csv.DictReader(f))


def csvout(name, rows, fields=None):
    fields = fields or list(dict.fromkeys(k for r in rows for k in r))
    with (OUT / name).open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def packed(obj):
    return json.dumps(obj, separators=(',', ':'), default=lambda x: float(x) if isinstance(x, Decimal) else str(x)).replace('</', '<\\/')


def dates_between(start, end):
    day, last = date.fromisoformat(start), date.fromisoformat(end)
    while day <= last:
        yield day.isoformat()
        day += timedelta(days=1)


def load_candles():
    ledger = json.loads((OUT / 'EXECUTION_LEDGER.json').read_text())
    manifest = json.loads((OUT / 'UPSTOX_REDUCED_MANIFEST.json').read_text())
    assert sha(OUT / 'UPSTOX_REDUCED_MANIFEST.json') == ledger['manifest_sha256'] == APPROVED
    assert ledger['complete'] and ledger['attempted_count'] == 16 and ledger['automatic_retries'] == 0
    allbars, coverage, quarantine, perday, date_gaps = [], [], [], [], []
    requests = {r['number']: r for r in manifest['requests']}
    records = ledger['requests'].copy()
    gift_path = OUT / 'additional/GIFT_EXECUTION_LEDGER.json'
    if gift_path.exists():
        from .execute_gift import APPROVED_GIFT
        gift = json.loads(gift_path.read_text())
        gift_plan = OUT / 'additional/PENDING_GIFT_MANIFEST.json'
        assert sha(gift_plan) == gift['manifest_sha256'] == APPROVED_GIFT
        assert gift['automatic_retries'] == 0
        requests.update({r['number']: r for r in json.loads(gift_plan.read_text())['requests']})
        records.extend(gift['requests'])
    # Inspect and reuse retained Dow; no new Dow API call.
    dow = next(r for r in json.loads((ROOT / 'artifacts/technical-integration-v1/retrieval-execution.json').read_text())['requests'] if r['series'] == 'DJIA')
    records.append(dict(dow, number='RETAINED_DOW', unit='days', interval=1, from_date='2025-12-01', to_date='2026-10-30'))
    requests['RETAINED_DOW'] = dict(number='RETAINED_DOW', series='DJIA', unit='days', interval=1, from_date='2025-12-01', to_date='2026-10-30')
    calendar = read(BASE / 'SESSION_CALENDAR_AUDIT.csv')
    cash_dates = {r['date'] for r in calendar if r['us_cash_session'] == 'True'}
    anomalies = []
    for rec in records:
        if 'retained_path' not in rec:
            continue
        path = ROOT / rec['retained_path']
        assert sha(path) == rec['response_sha256']
        raw = strict_payload(path.read_bytes())['data']['candles']
        req = requests[rec['number']]
        valid, bad, stats = validate(raw, req, rec['received_at'])
        for r in valid:
            r.update(source_file=rec['retained_path'], source_sha256=rec['response_sha256'],
                source_url=rec['url'], retrieved_at=rec['received_at'],
                price_units='PROVIDER_QUOTE_UNITS_UNVERIFIED', volume_semantics='ZERO_NOT_VERIFIED_TRADE_VOLUME',
                price_definition='UNVERIFIED_SPOT_FUTURE_OR_OTHER_QUOTATION' if r['series'] in ('BRENT', 'WTI', 'USDINR') else 'PROVIDER_BENCHMARK_DEFINITION_UNVERIFIED')
            if r['series'] in ('DJIA', 'US_TECH100') and r['unit'] == 'days' and r['provider_bucket_date'] not in cash_dates:
                anomalies.append(dict(series=r['series'], timestamp=r['provider_timestamp'],
                    reason='PROVIDER_BUCKET_LABEL_IS_NOT_A_US_CASH_SESSION;NO_GUESSED_DATE_SHIFT', source_file=r['source_file']))
        allbars.extend(valid)
        quarantine.extend(dict(r, source_file=rec['retained_path'], source_sha256=rec['response_sha256']) for r in bad)
        bydate = defaultdict(list)
        for r in valid:
            bydate[r['provider_bucket_date']].append(r)
        for day, bars in sorted(bydate.items()):
            stamps = sorted(datetime.fromisoformat(r['start_ist']) for r in bars)
            internal_absent = sum(max(0, int((b-a).total_seconds()/60)-1) for a, b in zip(stamps, stamps[1:])) if req['unit'] == 'minutes' else None
            perday.append(dict(number=rec['number'], series=req['series'], unit=req['unit'], date=day,
                bars=len(bars), first_start_ist=stamps[0].isoformat(), last_start_ist=stamps[-1].isoformat(),
                absent_grid_minutes_between_first_and_last=internal_absent,
                gap_semantics='ABSENT_GRID_POSITIONS;NOT_CERTIFIED_MISSING_TRADES_OR_OPEN_SESSION_MINUTES'))
        for day in dates_between(req['from_date'], req['to_date']):
            if day not in bydate:
                date_gaps.append(dict(number=rec['number'], series=req['series'], unit=req['unit'], date=day,
                    weekday_reference=date.fromisoformat(day).weekday() < 5,
                    reason='NO_ACCEPTED_PROVIDER_BUCKET_ON_CALENDAR_DATE;HOLIDAY_SESSION_AND_SPARSE_QUOTE_STATUS_UNVERIFIED'))
        coverage.append(dict(number=rec['number'], series=req['series'], unit=req['unit'],
            from_date=req['from_date'], to_date=req['to_date'], **stats,
            first_start_ist=valid[0]['start_ist'] if valid else '', last_start_ist=valid[-1]['start_ist'] if valid else '',
            observed_dates=len(bydate), calendar_dates_without_accepted_bars=sum(1 for d in dates_between(req['from_date'], req['to_date']) if d not in bydate),
            missing_expected_sessions='UNRESOLVED_PROVIDER_SESSION_AND_HOLIDAY_CALENDAR',
            price_unit_verified=False, historical_publication_verified=False,
            usable_as_verified_decision_time=0, usable_as_retrospective_provider_quotes=len(valid),
            source_file=rec['retained_path'], source_sha256=rec['response_sha256']))
    # Direction is a descriptive change within one exact provider series only.
    # This is market context, not a new stock technical indicator.
    for key in {r['series'] for r in allbars}:
        previous = None
        for row in sorted((r for r in allbars if r['series'] == key and r['unit'] == 'days'), key=lambda r: r['start_utc']):
            row['change_base_provider_timestamp'] = previous['provider_timestamp'] if previous else None
            row['change_previous_accepted_bucket_pct'] = 100 * (row['close'] / previous['close'] - 1) if previous else None
            row['direction_previous_accepted_bucket'] = ('UP' if row['close'] > previous['close'] else 'DOWN' if row['close'] < previous['close'] else 'FLAT') if previous else 'NO_BASELINE'
            row['change_basis'] = 'PREVIOUS_ACCEPTED_PROVIDER_BUCKET;NOT_VERIFIED_EXCHANGE_SESSION_RETURN'
            previous = row
    csvout('CANDLE_COVERAGE.csv', coverage)
    csvout('CANDLE_DATE_GAPS.csv', date_gaps)
    csvout('MINUTE_AND_DAILY_COVERAGE_BY_DATE.csv', perday)
    csvout('US_BUCKET_DATE_ANOMALIES.csv', anomalies, ['series', 'timestamp', 'reason', 'source_file'])
    save(OUT / 'QUARANTINED_CANDLES.json', quarantine)
    csvout('VALIDATED_PROVIDER_CANDLES.csv', allbars)
    return allbars, coverage, quarantine, calendar


def treasury():
    ledger_path = OUT / 'additional/PUBLIC_TREASURY_LEDGER.json'
    ledger = json.loads(ledger_path.read_text())
    if not ledger['complete']:
        return []
    rows = []
    for rec in ledger['requests']:
        path = ROOT / rec['retained_path']
        assert sha(path) == rec['response_sha256']
        tree = ET.fromstring(path.read_bytes())
        for entry in tree.findall('{http://www.w3.org/2005/Atom}entry'):
            prop = entry.find('.//{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}properties')
            values = {r.tag.split('}')[-1]: r.text for r in prop}
            day = values['NEW_DATE'][:10]
            if not '2025-11-20' <= day <= '2026-10-30':
                continue
            vals = {name: Decimal(values[key]) if values.get(key) not in (None, '', 'N/A') else None for name, key in [('UST2Y', 'BC_2YEAR'), ('UST10Y', 'BC_10YEAR'), ('UST30Y', 'BC_30YEAR')]}
            if any(v is not None and not v.is_finite() for v in vals.values()):
                raise ValueError('Nonfinite Treasury yield')
            quote = datetime.combine(date.fromisoformat(day), time(15, 30), NY)
            row = dict(date=day, **{k: float(v) if v is not None else None for k, v in vals.items()},
                spread_10y_2y_bp=float(spread_bp(vals['UST2Y'], vals['UST10Y'])) if all(vals[k] is not None for k in ('UST2Y', 'UST10Y')) else None,
                unit='PERCENT_PER_ANNUM_NOMINAL_PAR_CONSTANT_MATURITY', spread_unit='BASIS_POINTS',
                observation_timestamp_actual=None, observation_convention_ny=quote.isoformat(),
                observation_convention_ist=quote.astimezone(IST).isoformat(),
                observation_timing_basis='INDICATIVE_BID_SIDE_QUOTES_AT_OR_NEAR_15_30_NY;CONVENTION_NOT_EXACT_OBSERVATION_TIME',
                historical_available_at=None, publication_evidence='NOT_RETAINED_PER_HISTORICAL_OBSERVATION',
                atom_updated=entry.findtext('{http://www.w3.org/2005/Atom}updated'),
                atom_updated_basis='CURRENT_FEED_METADATA;NOT_HISTORICAL_RELEASE_TIME',
                source_file=rec['retained_path'], source_sha256=rec['response_sha256'], source_url=rec['url'], retrieved_at=rec['received_at'],
                vintage='CURRENT_RETRIEVAL;REVISIONS_NOT_EXCLUDED', decision_time_verified=False)
            rows.append(row)
    rows.sort(key=lambda r: r['date'])
    assert len({r['date'] for r in rows}) == len(rows)
    for index, row in enumerate(rows):
        previous = rows[index-1] if index else None
        row['change_base_date'] = previous['date'] if previous else None
        for key in ('UST2Y', 'UST10Y', 'UST30Y'):
            delta = yield_delta_bp(row[key], previous[key]) if previous else None
            row[key + '_change_bp'] = float(delta) if delta is not None else None
        row['spread_change_bp'] = float(Decimal(str(row['spread_10y_2y_bp'])) - Decimal(str(previous['spread_10y_2y_bp']))) if previous and row['spread_10y_2y_bp'] is not None and previous['spread_10y_2y_bp'] is not None else None
    csvout('TREASURY_YIELDS_AND_SPREAD.csv', rows)
    return rows


def contexts(bars, yields, calendar):
    sessions = [r['date'] for r in calendar if r['independent_expected_indian_session'] == 'True']
    groups = defaultdict(list)
    for r in bars:
        groups[(r['series'], r['unit'])].append(r)
    plan = json.loads((OUT / 'UPSTOX_REDUCED_MANIFEST.json').read_text())
    minute_windows = [r for r in plan['requests'] if r['unit'] == 'minutes']
    gift_executed = (OUT / 'additional/GIFT_EXECUTION_LEDGER.json').exists()
    if gift_executed:
        minute_windows.extend(r for r in json.loads((OUT / 'additional/PENDING_GIFT_MANIFEST.json').read_text())['requests'] if r['unit'] == 'minutes')
    result = []
    for day in sessions:
        for phase in PHASES:
            cut = checkpoint(day, phase)
            for series in ('USDINR', 'BRENT', 'WTI', 'US_TECH100', 'DJIA') + (('GIFT_NIFTY',) if gift_executed else ()):
                for unit in ('days', 'minutes') if series != 'DJIA' else ('days',):
                    candidates = groups[(series, unit)]
                    if unit == 'minutes':
                        window = next((r for r in minute_windows if r['series'] == series and r['from_date'] <= day <= r['to_date']), None)
                        candidates = [r for r in candidates if window and r['number'] == window['number']]
                    chosen = latest_completed(candidates, cut)
                    reason = ('NO_REQUESTED_MINUTE_WINDOW_FOR_CHECKPOINT;NO_FILL' if unit == 'minutes' and not window else 'NO_EARLIER_COMPLETED_BAR_IN_THIS_WINDOW' if unit == 'minutes' else 'NO_EARLIER_PROVIDER_DAY_BUCKET') if not chosen else 'HISTORICAL_DELIVERY_AND_PRICE_DEFINITION_UNVERIFIED'
                    age = (cut-datetime.fromisoformat(chosen['end_ist'])).total_seconds()/60 if chosen else None
                    result.append(dict(indian_session=day, phase=phase, cutoff_ist=cut.isoformat(), series=series,
                        channel='PROVIDER_DAILY' if unit == 'days' else 'SAMPLED_MINUTE', label=LABELS[series],
                        verified_value=None, retrospective_value=chosen['close'] if chosen else None,
                        units='PROVIDER_QUOTE_UNITS_UNVERIFIED', category='RETROSPECTIVE_ONLY' if chosen else 'UNAVAILABLE',
                        observation_date=chosen['provider_bucket_date'] if chosen else None,
                        source_observation_timestamp=chosen['provider_timestamp'] if chosen else None,
                        bar_end_ist=chosen['end_ist'] if chosen else None,
                        completion_before_cutoff=bool(chosen), completion_basis='DAILY_BUCKET_END_IS_NOT_EXCHANGE_CLOSE' if unit == 'days' else 'ONE_MINUTE_END_STRICTLY_BEFORE_CUTOFF',
                        age_minutes=age, stale_status='OLDER_THAN_24H_DESCRIPTIVE_FLAG' if age is not None and age > 1440 else 'WITHIN_24H' if age is not None else 'NO_OBSERVATION',
                        change_bp=None, historical_available_at=None, decision_time_verified=False, reason=reason,
                        direction=chosen.get('direction_previous_accepted_bucket') if chosen else None,
                        change_pct=chosen.get('change_previous_accepted_bucket_pct') if chosen else None,
                        change_base_timestamp=chosen.get('change_base_provider_timestamp') if chosen else None,
                        source_file=chosen['source_file'] if chosen else None, source_sha256=chosen['source_sha256'] if chosen else None,
                        source_url=chosen['source_url'] if chosen else None,
                        retrieved_at=chosen['retrieved_at'] if chosen else None))
            eligible_yields = [r for r in yields if datetime.fromisoformat(r['observation_convention_ist']) < cut]
            chosen_yield = max(eligible_yields, key=lambda r: r['date'], default=None)
            for key in ('UST2Y', 'UST10Y', 'UST30Y', 'UST10Y2Y'):
                value_key = 'spread_10y_2y_bp' if key == 'UST10Y2Y' else key
                change_key = 'spread_change_bp' if key == 'UST10Y2Y' else key + '_change_bp'
                row = chosen_yield
                age = (cut-datetime.fromisoformat(row['observation_convention_ist'])).total_seconds()/60 if row else None
                result.append(dict(indian_session=day, phase=phase, cutoff_ist=cut.isoformat(), series=key,
                    channel='OFFICIAL_TREASURY_DAILY', label=LABELS[key], verified_value=None,
                    retrospective_value=row[value_key] if row else None,
                    units='BASIS_POINTS' if key == 'UST10Y2Y' else 'PERCENT_PER_ANNUM',
                    category='RETROSPECTIVE_ONLY' if row and row[value_key] is not None else 'UNAVAILABLE',
                    observation_date=row['date'] if row else None, source_observation_timestamp=None,
                    observation_convention_ist=row['observation_convention_ist'] if row else None,
                    observation_timing_basis='APPROXIMATE_15_30_NY_QUOTE_CONVENTION;NOT_RELEASE_TIME',
                    completion_before_cutoff=None, age_minutes=age,
                    stale_status='OLDER_THAN_4_CALENDAR_DAYS' if age is not None and age > 5760 else 'WITHIN_4_CALENDAR_DAYS' if age is not None else 'NO_OBSERVATION',
                    change_bp=row[change_key] if row else None, change_base_date=row['change_base_date'] if row else None,
                    historical_available_at=None, decision_time_verified=False,
                    reason='CURRENT_VINTAGE;HISTORICAL_PUBLICATION_NOT_ATTESTED;NO_SAME_INDIAN_DATE_US_QUOTE',
                    source_file=row['source_file'] if row else None, source_sha256=row['source_sha256'] if row else None,
                    source_url=row['source_url'] if row else None,
                    retrieved_at=row['retrieved_at'] if row else None))
            for key in (() if gift_executed else ('GIFT_NIFTY',)) + ('NASDAQCOM', 'ES', 'NQ'):
                result.append(dict(indian_session=day, phase=phase, cutoff_ist=cut.isoformat(), series=key,
                    channel='MISSING_REQUIRED_COMPONENT', label=LABELS[key], verified_value=None, retrospective_value=None,
                    category='UNAVAILABLE', units='UNVERIFIED', change_bp=None,
                    decision_time_verified=False, historical_available_at=None,
                    reason='PENDING_SEPARATE_FOUR_REQUEST_GIFT_AUTHORIZATION;GENERIC_QUOTE_NOT_CONTRACT' if key == 'GIFT_NIFTY' else 'NO_VERIFIED_EXACT_UPSTOX_EQUIVALENT;LICENSED_ALTERNATIVE_REQUIRED'))
    csvout('INDIAN_CHECKPOINT_CONTEXT.csv', result)
    return result, sessions


def components(bars, yields, context):
    output = []
    for key, label in LABELS.items():
        source_bars = [r for r in bars if r['series'] == key]
        daily = [r for r in source_bars if r['unit'] == 'days']
        minute = [r for r in source_bars if r['unit'] == 'minutes']
        is_yield = key.startswith('UST')
        count = sum(r['UST2Y'] is not None and r['UST10Y'] is not None for r in yields) if key == 'UST10Y2Y' else sum(r[key] is not None for r in yields) if is_yield else len(daily)
        state = 'PARTIAL' if count or minute else 'UNAVAILABLE'
        notes = ('Official nominal par yields and same-date spread integrated as retrospective values; historical publication/vintage unavailable.' if is_yield else
            'Retained Dow reused; exact price-index identity, US session mapping and historical publication unresolved.' if key == 'DJIA' else
            'Generic GIFT quotation history integrated; no identified contract/expiry, price-unit definition or historical delivery certification.' if key == 'GIFT_NIFTY' and (count or minute) else
            'Generic catalog key exists; no accepted quotation history. Inspect separate GIFT execution ledger. No identified GIFT futures contract.' if key == 'GIFT_NIFTY' else
            'No confirmed exact Upstox equivalent; no unrelated substitution. Minimum licensed alternative documented.' if key in ('ES', 'NQ', 'NASDAQCOM') else
            'Daily and three sample minute windows integrated; units/benchmark definition and historical delivery unresolved. US Tech 100 is not Nasdaq Composite or NQ.' if key == 'US_TECH100' else
            'Provider daily and sample minute history integrated; quote definition/units and historical delivery unresolved. Other retained macro vintages remain separate.')
        output.append(dict(series=key, label=label, status=state, supported_integration=bool(count or minute),
            accepted_daily_observations=count, accepted_minute_observations=len(minute),
            first_date=min((r['date'] for r in yields), default=None) if is_yield else min((r['provider_bucket_date'] for r in daily), default=None),
            last_date=max((r['date'] for r in yields), default=None) if is_yield else max((r['provider_bucket_date'] for r in daily), default=None),
            verified_preopen_values=0, verified_postclose_values=0,
            descriptive_preopen_candidates=sum(r['series'] == key and r['phase'] == 'PREOPEN' and r.get('retrospective_value') is not None for r in context),
            descriptive_postclose_candidates=sum(r['series'] == key and r['phase'] == 'POSTCLOSE' and r.get('retrospective_value') is not None for r in context),
            sampled_minute_preopen_checkpoints=sum(r['series'] == key and r['phase'] == 'PREOPEN' and r['channel'] == 'SAMPLED_MINUTE' and r.get('retrospective_value') is not None for r in context),
            sampled_minute_postclose_checkpoints=sum(r['series'] == key and r['phase'] == 'POSTCLOSE' and r['channel'] == 'SAMPLED_MINUTE' and r.get('retrospective_value') is not None for r in context),
            limitation=notes))
    csvout('GLOBAL_COMPONENT_STATUS.csv', output)
    return output


def integrate(context, bars, yields, status, sessions):
    # Only append presentation scripts to existing chart HTML; embedded data and
    # every pre-existing asset are byte-for-byte preserved.
    ledger = []
    baseline = json.loads((OUT / 'PRESERVATION_BASELINE.json').read_text())
    for path, digest in baseline.items():
        p = ROOT / path
        if p.suffix == '.html' and p.parent.name in ('stocks', 'indices') and p.name != 'index.html':
            text = p.read_text()
            original = text.replace(INJECTION, '')
            import hashlib
            assert hashlib.sha256(original.encode()).hexdigest() == digest, path
            if INJECTION not in text:
                anchor = '<script src="assets/chart.js"></script>'
                assert text.count(anchor) == 1
                p.write_text(text.replace(anchor, INJECTION + anchor))
            ledger.append(dict(chart=str(p.relative_to(ROOT)), preexisting_bytes_reconstruct_sha256=digest,
                               embedded_indicator_and_subsector_payload_unchanged=True))
        elif p == CHARTS / 'index.html':
            import hashlib
            assert hashlib.sha256(p.read_text().replace(INDEX_LINK, '').encode()).hexdigest() == digest, path
        else:
            assert sha(p) == digest, path
    byday = defaultdict(list)
    for r in context:
        byday[r['indian_session']].append(r)
    data = dict(status=status, context=byday, sessions=sessions, treasury=yields)
    (CHARTS / 'global-market-data.js').write_text('window.TECH06_GLOBAL=' + packed(data) + ';\n')
    (CHARTS / 'global-minute-data.js').write_text('window.TECH06_GLOBAL_BARS=' + packed([
        dict(series=r['series'], unit=r['unit'], number=r['number'], timestamp=r['provider_timestamp'],
             time=int(datetime.fromisoformat(r['start_utc']).timestamp()), date=r['provider_bucket_date'],
             open=r['open'], high=r['high'], low=r['low'], close=r['close'], end_ist=r['end_ist'], source_file=r['source_file']) for r in bars]) + ';\n')
    from .narrowed_charts import CONTEXT_JS, PAGE
    (CHARTS / 'global-context.js').write_text(CONTEXT_JS)
    (CHARTS / 'global-market.html').write_text(PAGE)
    # Add a discoverable entry without rewriting the established index.
    index = CHARTS / 'index.html'
    link = INDEX_LINK
    text = index.read_text()
    if link not in text:
        index.write_text(text + link)
    save(OUT / 'INTEGRATION_PRESERVATION.json', dict(
        checked_baseline_files=len(baseline), preexisting_nonpresentation_files_unchanged=True,
        original_chart_payloads_unchanged=True, original_broad_manifest_unchanged=True,
        additions='Global context scripts before existing chart.js, plus global-market entry. No formulas, validated indicator fields or subsector rows changed.', charts=ledger))


def main():
    bars, coverage, quarantine, calendar = load_candles()
    yields = treasury()
    context, sessions = contexts(bars, yields, calendar)
    status = components(bars, yields, context)
    integrate(context, bars, yields, status, sessions)
    new = [r for r in coverage if r['number'] != 'RETAINED_DOW' and r['series'] != 'GIFT_NIFTY']
    gift_coverage = [r for r in coverage if r['series'] == 'GIFT_NIFTY']
    gift_path = OUT / 'additional/GIFT_EXECUTION_LEDGER.json'
    gift = json.loads(gift_path.read_text()) if gift_path.exists() else None
    save(OUT / 'RESULT_SUMMARY.json', dict(
        approved_manifest_sha256=APPROVED, executed_requests=16, retries=0,
        returned_daily_bars=sum(r['returned'] for r in new if r['unit'] == 'days'),
        accepted_daily_bars=sum(r['accepted'] for r in new if r['unit'] == 'days'),
        returned_minute_bars=sum(r['returned'] for r in new if r['unit'] == 'minutes'),
        accepted_minute_bars=sum(r['accepted'] for r in new if r['unit'] == 'minutes'),
        quarantined_candles=len(quarantine), treasury_dates_including_baseline=len(yields),
        treasury_study_dates=sum('2025-12-01' <= r['date'] <= '2026-10-30' for r in yields),
        retained_dow_bars=sum(r['series'] == 'DJIA' for r in bars),
        indian_sessions=len(sessions), checkpoint_rows=len(context),
        verified_preopen_values=0, verified_postclose_values=0,
        actual_historical_publication_timestamps_recovered=0,
        gift_executed_requests=gift['attempted_count'] if gift else 0,
        gift_accepted_daily_bars=sum(r['accepted'] for r in gift_coverage if r['unit'] == 'days'),
        gift_accepted_minute_bars=sum(r['accepted'] for r in gift_coverage if r['unit'] == 'minutes'),
        pending_authenticated_gift_requests=0 if gift else 4, paid_retrievals=0,
        narrowed_scope_complete=True, additional_scope_supported_integration_complete=True,
        remaining_authorization_blockers=([] if gift else ['Four GIFT quote requests']) + ['Exact Nasdaq Composite research/distribution rights and licensed history', 'ES/NQ contract-specific historical data quote and license'],
        components=status))
    print((OUT / 'RESULT_SUMMARY.json').read_text())


if __name__ == '__main__':
    main()
