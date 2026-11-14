"""Read-only adapter over the canonical Passport candle interface."""
from decimal import Decimal


def daily_prices(store, company_id, symbol, *, start, end, decision_time):
    result = store.read(company_id, 'Daily', start=start, end=end,
                        decision_time=decision_time, mode='retrospective')
    rows = []
    for r in result['rows']:
        rows.append(dict(symbol=symbol, instrument_key=r['instrument_key'], date=r['start'][:10],
                         **{'raw_'+k: Decimal(str(r[k])) for k in ('open','high','low','close','volume')},
                         original_timestamp=r['start'], available_at=r.get('end') or r['end_upper_bound'],
                         source_response_sha256s=r['source_response_sha256s'],
                         data_quality_flags=r['data_quality_flags']))
    return dict(rows=rows, exclusions=result['exclusions'], source_revisions=result['source_revisions'],
                adjustment_certification='PROVIDER_BASIS_UNCERTIFIED;NO_AUTOMATIC_ACTION_ADJUSTMENT')
