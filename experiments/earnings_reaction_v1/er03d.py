"""Offline ER03D gap audit. Never reads credentials or makes network calls."""
from datetime import date
from .er03 import BASE, ROOT, OUT as ER, read_csv, write_csv, write_json, digest
from .er03_chart_upgrade import load_inputs
from .upstox_candles import url_for
from .retrieve_er03 import DailyRequest

OUT = ER/'data-completion'

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    before = {str(p.relative_to(ROOT)):digest(p) for p in BASE.rglob('*')
              if p.is_file() and OUT not in p.parents and BASE/'er04' not in p.parents and p.name!='.DS_Store'}
    if not (OUT/'preservation-before.json').exists():
        write_json(OUT/'preservation-before.json', before)
    data, sources = load_inputs()
    pilots=read_csv(ER/'pilot-companies.csv')
    windows=read_csv(ER/'approximate-event-windows.csv')
    reference=next(p for p in pilots if p['symbol']=='TIPSMUSIC')
    calendar=[str(c.start_ist.date()) for c in data[reference['instrument_key']]]
    gaps=[]; requests=[]; dates=[]
    for p in pilots:
        bars=data[p['instrument_key']]
        first=str(bars[0].start_ist.date())
        target=min(w['observed_first_date'] for w in windows if w['symbol']==p['symbol'])
        retained_prior=sum(str(c.start_ist.date())<target for c in bars)
        missing=max(0,251-retained_prior)
        prior=[d for d in calendar if d<target]
        if len(prior)<251: raise ValueError('Insufficient reference sessions for bounded proposal')
        start=prior[-251]
        candidates=[d for d in prior[-251:] if d<first] if missing else []
        for d in candidates: dates.append(dict(symbol=p['symbol'],date=d,status='EXPECTED_REFERENCE_SESSION;STOCK_OBSERVATION_NOT_YET_ACQUIRED'))
        gaps.append(dict(symbol=p['symbol'],first_retained=first,last_retained=str(bars[-1].start_ist.date()),
                         retained_bars=len(bars),first_research_session=target,prior_bars=retained_prior,
                         additional_prior_bars_required=missing,proposed_from=start if missing else '',
                         proposed_to=str(date.fromisoformat(first).fromordinal(date.fromisoformat(first).toordinal()-1)) if missing else '',
                         expected_dates=len(candidates),calendar_status='RETAINED_TIPSMUSIC_OBSERVED_SESSIONS;NOT_VERIFIED_STOCK_CALENDAR'))
        if missing:
            q=DailyRequest(p['instrument_key'],'days',1,date.fromisoformat(start),date.fromisoformat(gaps[-1]['proposed_to']))
            requests.append(dict(number=len(requests)+1,symbol=p['symbol'],instrument_key=q.instrument_key,
                                 unit=q.unit,interval=q.interval,from_date=str(q.from_date),to_date=str(q.to_date),url=url_for(q),minimum_rows_required=missing))
    write_csv(OUT/'historical-gaps.csv',gaps)
    write_csv(OUT/'expected-missing-session-dates.csv',dates)
    old=__import__('json').loads((ER/'acquisition-manifest.json').read_text())
    manifest=dict(scope='ER-03D:RESEARCH-WINDOW-SIMPLE-VOLUME-CONTEXT',status='AWAITING_EXPLICIT_OWNER_APPROVAL',
                  request_count=len(requests),requests=requests,metadata_path=old['metadata_path'],metadata_sha256=old['metadata_sha256'],
                  automatic_retries=0,execution_policy=old['execution_policy'],mechanism=old['mechanism'],
                  retention_location=str((OUT/'provider').relative_to(ROOT)),
                  existing_authorization='ER03 exact manifest exhausted; no authorization for these earlier intervals',
                  objective='251 prior bars at first research session; no existing candle dates requested',
                  calendar_limit='Dates inferred from real retained TIPSMUSIC sessions only. Verify per-stock counts after response; no invented observations or automatic expansion.',
                  hve_limit='Rolling 252-bar color initialization only; does not establish complete listing history or exact HVE/HVY identity',
                  network_requests_made=0,token_accessed=False)
    write_json(OUT/'acquisition-manifest.json',manifest)
    (OUT/'acquisition-manifest.sha256').write_text(digest(OUT/'acquisition-manifest.json')+'\n')
    write_json(OUT/'acquisition-receipt.json',dict(status='NOT_EXECUTED_AUTHORIZATION_REQUIRED',authenticated_calls=0,credentials_accessed=False))
    write_json(OUT/'input-provenance.json',sources)
    (OUT/'HISTORICAL_GAP_REPORT.md').write_text('''# ER-03D historical context audit

Final daily Simple Volume color requires **252 bars inclusive of current bar**, hence 251 prior bars. PP10 needs ten prior chart bars; SMA21 needs 21 inclusive; RVOL21 needs 21 prior; U/D20 needs twenty prior; turnover50 needs fifty inclusive; quarter low needs63 inclusive. EMA starts at first retained close; ATR14 needs14 prior true ranges; DSS20 needs20 prior; RS55 needs56 exactly benchmark-aligned bars. No formulas changed.

The attached CSV lists each missing expected session date and exact bounded GET proposal. These are observed TIPSMUSIC reference sessions, not certified missing stock observations: per-stock suspensions, special sessions and calendar completeness remain uncertain until real responses arrive. Requests end before each stock's existing first candle. They target the research periods, not full classification of every older warmup candle. AETHER also needs earlier context for Q4; TIPSMUSIC research periods already have enough pure Upstox context.

The old ER03 approval is bound to a different SHA256 and all12 requests were executed. Fresh approval is required for the11 proposed requests. No credentials accessed, no authenticated requests made, no production acquisition changes. Receipt explicitly records non-execution. A full completed-coverage report and updated Phase A charts remain blocked by this authorization.

252 bars resolve rolling-window colors but cannot prove all-history HVE or distinguish every HVE/HVY label. Complete listing history is not attested. Existing TIPSMUSIC chart uses an explicitly disclosed TradingView volume prefix for records; new ER04 calculations use only Upstox candles, so do not inherit mixed-provider record context. Original ER03C charts and evidence remain unchanged. Provider adjustment basis and exact cross-feed parity remain unresolved.
''')
    print(digest(OUT/'acquisition-manifest.json'),len(requests),'requests')

if __name__=='__main__': main()
