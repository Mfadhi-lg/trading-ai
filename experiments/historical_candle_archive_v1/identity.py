"""Resolve existing company identifiers without depending on registry-link completeness.

No names are guessed, and no canonical company/security tables are modified.
An exact scraped exchange code/symbol joins the existing exchange master, then
its ISIN joins provider metadata. Conflicting current ISINs fail closed.
"""
from collections import defaultdict
import re


class IdentityResolver:
    def __init__(self, securities, identifiers, scopes, provider_records):
        self.securities=defaultdict(list)
        self.identifiers=defaultdict(list)
        self.scopes=defaultdict(list)
        self.provider=defaultdict(list)
        self.provider_identifier=defaultdict(list)
        for r in securities:
            token=r.get('nse_symbol') if r['exchange']=='NSE' else r.get('bse_scrip_code')
            if token: self.securities[(r['exchange'],str(token))].append(r)
        for r in identifiers:
            if r.get('is_current',1) and not r.get('valid_to'):
                self.identifiers[r['company_id']].append(r)
        for r in scopes:
            if r.get('company_id') is not None:self.scopes[r['company_id']].append(r)
        for r in provider_records:
            if r.get('segment') in ('NSE_EQ','BSE_EQ') and re.fullmatch(r'INE[A-Z0-9]{9}',r.get('isin','')):
                self.provider[r['isin']].append(r)
                token=r['trading_symbol'] if r['exchange']=='NSE' else str(r['exchange_token'])
                self.provider_identifier[(r['exchange'],token)].append(r)

    def resolve(self, company_id):
        anchors=[]
        for r in self.identifiers[company_id]:
            if r['identifier_type'] in ('NSE','BSE'):
                anchors.append((r['identifier_type'],str(r['identifier_value']),
                                dict(table='security_identifiers',row=r)))
        for r in self.scopes[company_id]:
            match=re.fullmatch(r'/company/([^/]+)/(?:consolidated/)?',r['company_path'])
            if match:
                token=match.group(1)
                anchors.append(('BSE' if token.isdigit() else 'NSE',token,
                                dict(table='screener_collection_scope',company_path=r['company_path'],
                                     observed_at=r['observed_at_utc'])))
        evidence=[];conflicts=[];candidates={};anchored_isins=set();direct={}
        for exchange,token,source in anchors:
            exact=self.provider_identifier[(exchange,token)]
            if len({r['instrument_key'] for r in exact})>1:
                return dict(status='AMBIGUOUS_PROVIDER_RECORDS',instrument_key=None,
                            evidence=evidence,conflicts=conflicts,
                            known_identifiers=[dict(exchange=e,value=t) for e,t,_ in anchors])
            if len({r['instrument_key'] for r in exact})==1:
                m=exact[0];direct[m['instrument_key']]=m
                evidence.append(dict(source=source,exchange=exchange,identifier=token,isin=m['isin'],
                                     basis='EXACT_EXISTING_IDENTIFIER_TO_PROVIDER_RECORD',
                                     provider_instrument_key=m['instrument_key']))
            matches=self.securities[(exchange,token)]
            for s in matches:
                isin=s.get('current_isin')
                if not isin: continue
                anchored_isins.add(isin)
                proof=dict(source=source,exchange=exchange,identifier=token,
                           exchange_security_id=s['exchange_security_id'],isin=isin,
                           exchange_status=s['current_status'],verification_status=s.get('verification_status'),
                           basis='EXACT_EXISTING_EXCHANGE_IDENTIFIER_TO_ISIN')
                evidence.append(proof)
                for m in self.provider[isin]:
                    # Same-exchange code disagreement is evidence of stale metadata,
                    # not permission to choose a matching company name.
                    if m['exchange']==exchange:
                        provider_token=m['trading_symbol'] if exchange=='NSE' else str(m['exchange_token'])
                        if provider_token!=token:
                            conflicts.append(dict(kind='SYMBOL_OR_CODE_CHANGED_SAME_ISIN',existing_identifier=token,
                                                  provider_identifier=provider_token,isin=isin))
                    candidates[m['instrument_key']]=m
        if len(anchored_isins)>1:
            return dict(status='CONFLICTING_EXISTING_CURRENT_ISINS',instrument_key=None,
                        evidence=evidence,conflicts=conflicts,known_identifiers=[dict(exchange=e,value=t) for e,t,_ in anchors])
        direct_isins={m['isin'] for m in direct.values()}
        if len(direct_isins)>1:
            return dict(status='CONFLICTING_PROVIDER_IDENTIFIERS',instrument_key=None,
                        evidence=evidence,conflicts=conflicts)
        changed_isin=bool(direct_isins and anchored_isins and direct_isins!=anchored_isins)
        if direct:
            if changed_isin:
                conflicts.append(dict(kind='PROVIDER_CURRENT_ISIN_DIFFERS_FROM_RETAINED_EXCHANGE_MASTER',
                                      retained_isins=sorted(anchored_isins),provider_isins=sorted(direct_isins),
                                      historical_continuity='UNVERIFIED; preserve old identities and review effective dates'))
            candidates=direct if changed_isin else {**candidates,**direct}
        if not candidates:
            return dict(status='PROVIDER_ISIN_NOT_PRESENT' if anchored_isins else 'EXCHANGE_MASTER_IDENTITY_NOT_LINKED',
                        instrument_key=None,evidence=evidence,conflicts=conflicts,
                        known_identifiers=[dict(exchange=e,value=t) for e,t,_ in anchors])
        # Require one provider security per exchange/ISIN. Prefer the primary NSE
        # quotation when the verified underlying ISIN has both exchange listings.
        preferred=[r for r in candidates.values() if r['exchange']=='NSE'] or list(candidates.values())
        if len(preferred)!=1:
            return dict(status='AMBIGUOUS_PROVIDER_RECORDS',instrument_key=None,evidence=evidence,conflicts=conflicts)
        m=preferred[0]
        return dict(status='EXACT_IDENTIFIER_CURRENT_ISIN_CHANGED; HISTORY_REVIEW_REQUIRED' if changed_isin else
                    'EXACT_EXISTING_IDENTIFIER_AND_ISIN_OVERLAY',instrument_key=m['instrument_key'],
                    symbol=m['trading_symbol'],isin=m['isin'],exchange=m['exchange'],
                    instrument_type=m.get('instrument_type'),provider_name=m.get('name'),
                    historical_isins=sorted(anchored_isins),historical_continuity_verified=False,
                    evidence=evidence,conflicts=conflicts,
                    historical_identity='CURRENT_MAPPING_ONLY; retain dated security versions for historical joins')
