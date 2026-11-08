"""Repair archive identity overlays only; no acquisition or company-master writes."""
from collections import Counter
import gzip
import json
from .identity import IdentityResolver
from .prepare import METADATA,requests,csvout
from .storage import OUT,dump,sha,atomic


def main():
    sources=['eligible-universe.json','eligible-universe.csv','universe-summary.json','full-universe-manifest.json']
    before={name:(OUT/name).read_bytes() for name in sources}
    tables=json.loads((OUT/'latest-acquisition-tables.json').read_text())
    bse_path=OUT/'public-bse-instruments.json.gz'
    metadata=json.loads(gzip.decompress(METADATA.read_bytes()))+json.loads(gzip.decompress(bse_path.read_bytes()))
    resolver=IdentityResolver(tables['exchange_securities'],tables['security_identifiers'],
                              tables['screener_collection_scope'],metadata)
    universe=json.loads(before['eligible-universe.json']);repairs=[]
    for r in universe:
        if r.get('instrument_key'):continue
        resolved=resolver.resolve(r['company_id'])
        repairs.append(dict(company_id=r['company_id'],name=r['name'],**resolved))
        r['known_exchange_identifiers']=json.dumps(resolved.get('known_identifiers',
            [dict(exchange=e['exchange'],value=e['identifier']) for e in resolved['evidence']]),sort_keys=True)
        r['mapping_status']=resolved['status']
        if resolved['instrument_key']:
            r.update(instrument_key=resolved['instrument_key'],symbol=resolved['symbol'],
                     provider_isin=resolved['isin'],provider_instrument_type=resolved['instrument_type'],
                     historical_isins=json.dumps(resolved['historical_isins']),
                     historical_identity_continuity='UNVERIFIED; retain old ISINs and dated versions')
    keys=[r['instrument_key'] for r in universe if r['instrument_key']]
    if len(keys)!=len(set(keys)):raise ValueError('Provider identity claimed by multiple canonical companies')
    original=json.loads(before['eligible-universe.json'])
    if [(r['company_id'],r['market_cap_crore'],r['market_cap_observed_at']) for r in original]!=[
            (r['company_id'],r['market_cap_crore'],r['market_cap_observed_at']) for r in universe]:
        raise ValueError('Eligibility/canonical identity changed during identity-only repair')
    for old,new in zip(original,universe):
        if old.get('instrument_key') and old!=new:raise ValueError('Existing resolved identity changed')
    manifest=json.loads(before['full-universe-manifest.json'])
    if manifest.get('execution_enabled') is not False or manifest.get('approval_required') is not True:
        raise ValueError('Full-universe execution gate must stay disabled')
    manifest.update(companies=universe,requests=[q for r in universe for q in requests(r)]+
                    [q for r in manifest.get('historically_eligible_extension',[]) for q in requests(r)])
    summary=json.loads(before['universe-summary.json'])
    summary.update(supported_exact_nse_companies=sum(bool(r['instrument_key'] and r['instrument_key'].startswith('NSE')) for r in universe),
                   supported_exact_bse_companies=sum(bool(r['instrument_key'] and r['instrument_key'].startswith('BSE')) for r in universe),
                   unresolved_companies=sum(not r['instrument_key'] for r in universe),full_native_requests=len(manifest['requests']))
    # Preserve prior research and leave the pilot manifest/receipts untouched.
    backup=OUT/'identity-repair-before'
    for name,raw in before.items():
        target=backup/name
        if not target.exists():target.parent.mkdir(parents=True,exist_ok=True);atomic(target,raw)
    dump(OUT/'identity-linkage-repair.json',dict(company_master_modified=False,pilot_manifest_modified=False,
        acquisition_executed=False,full_universe_execution_enabled=False,
        prior_unresolved=len(repairs),resolved=sum(bool(r['instrument_key']) for r in repairs),
        remaining=sum(not r['instrument_key'] for r in repairs),
        status_counts=Counter(r['status'] for r in repairs),records=repairs,
        source_checksums=dict(nse_provider=sha(METADATA.read_bytes()),bse_provider=sha(bse_path.read_bytes()),
                              acquisition_tables=sha((OUT/'latest-acquisition-tables.json').read_bytes())),
        input_checksums={name:sha(raw) for name,raw in before.items()},
        limitation='Provider identifiers establish a current mapping, not historical ISIN continuity or corporate-action adjustment rules'))
    dump(OUT/'eligible-universe.json',universe)
    csvout(OUT/'eligible-universe.csv',universe)
    dump(OUT/'universe-summary.json',summary)
    dump(OUT/'full-universe-manifest.json',manifest)
    print(json.dumps(dict(repaired=sum(bool(r['instrument_key']) for r in repairs),remaining=summary['unresolved_companies'],
                          nse=summary['supported_exact_nse_companies'],bse=summary['supported_exact_bse_companies'],
                          planned_requests=summary['full_native_requests'],execution_enabled=False),indent=2))

if __name__=='__main__':main()
