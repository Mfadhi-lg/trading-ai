"""Finite official-page discovery from retained issuer/source identity.

No guessed exchange API, domain search, manifest, or reporting-period fallback.
Same-host issuer links only; ratings keep the existing publisher restrictions.
"""
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit,urljoin,urlencode,parse_qs
import json,re
from calendar import monthrange
from bs4 import BeautifulSoup
from ...core.access_policy import is_blocked_terms_target
from ...db.document_artifact_repo import persist_discovery_plan
from ...db.host_permits import reviewed_policy
from ...documents.revisions import enqueue_revision
from .discovery import _parse_page,DiscoveryPlan


def allowed_page(conn,url):
    parsed=urlsplit(url)
    return (parsed.scheme=='https' and bool(parsed.hostname) and not parsed.username and
            not parsed.password and parsed.hostname.lower()!='api.bseindia.com' and
            not is_blocked_terms_target(url) and not reviewed_policy(conn,url).blocked)


def company_pages(conn,company_id,*,permitted_only=True,now=None):
    # Previously verified IR/official pages are preferred to issuer homepages.
    pages=[(r[0],r[1]) for r in conn.execute('''SELECT discovered_from_source,discovery_page_url
        FROM artifact_discovery_pages WHERE company_id=? AND discovered_from_source IN ('company_ir','nse','bse')
        GROUP BY discovered_from_source,discovery_page_url ORDER BY max(first_observed_at_utc) DESC LIMIT 2''',(company_id,))]
    if not pages:
        pages=[('company_ir',r[0]) for r in conn.execute('''SELECT DISTINCT p.source_url
            FROM company_profile_links p JOIN accepted_page_versions a USING(parse_id)
            WHERE a.company_id=? AND a.source='screener' AND p.link_type='website'
            ORDER BY a.accepted_at_utc DESC LIMIT 1''',(company_id,))]
    symbol=conn.execute("SELECT identifier_value FROM security_identifiers WHERE company_id=? AND identifier_type='NSE' AND is_current=1 LIMIT 1",(company_id,)).fetchone()
    if symbol:
        from datetime import timedelta
        from ...documents.availability import parse_utc
        from ...db.repo import utc_now
        day=parse_utc(now or utc_now()).date()
        query=urlencode({'index':'equities','symbol':symbol[0],'from_date':(day-timedelta(days=21)).strftime('%d-%m-%Y'),'to_date':day.strftime('%d-%m-%Y')})
        # Existing official NSE resolver endpoint, one bounded page, no BSE API.
        pages=pages[:1]+[('nse','https://www.nseindia.com/api/corporate-announcements?'+query)]
    return [(source,url) for source,url in pages if not permitted_only or allowed_page(conn,url)]


def ingest_page(conn, *, company_id, source, url, body, now, snapshot_root):
    if source not in {'company_ir','nse','bse','rating_agency'} or not allowed_page(conn,url):
        raise ValueError('Official source access blocked')
    company=conn.execute('SELECT current_legal_name FROM companies WHERE company_id=?',(company_id,)).fetchone()
    if company is None: raise ValueError('Unknown company')
    symbol=f'company-{company_id}'
    if source=='nse' and urlsplit(url).path=='/api/corporate-announcements':
        page,candidates=announcement_page(conn,company_id=company_id,url=url,body=body,now=now)
    else:
        page,candidates=_parse_page(symbol=symbol,page_position=0,source=source,page_url=url,
            publisher_claim=company[0] if source=='company_ir' else None,content_path=Path('routine.html'),content=body)
    # Only explicit Qn FY-ending-year labels establish a fiscal quarter. A
    # publication month, current date or result expectation never supplies it.
    enriched=[]
    for candidate in candidates:
        match=re.search(r'\bQ([1-4])\s*[-_/ ]*\s*FY\s*(20\d{2}|\d{2})\b',candidate.title_raw,re.I)
        if candidate.period_end is None and match:
            quarter=int(match[1]);fy=int(match[2]);fy=fy if fy>=2000 else 2000+fy
            month={1:6,2:9,3:12,4:3}[quarter];year=fy if quarter==4 else fy-1
            candidate=replace(candidate,period_end=f'{year:04d}-{month:02d}-{monthrange(year,month)[1]:02d}',fiscal_year=fy,fiscal_quarter=quarter)
        enriched.append(candidate)
    candidates=enriched
    host=urlsplit(url).hostname
    candidates=[c for c in candidates if allowed_page(conn,c.document_url) and
        (source!='company_ir' or urlsplit(c.document_url).hostname==host)]
    prior={}
    for c in candidates:
        row=conn.execute('''SELECT s.*,a.artifact_type FROM artifact_sources s JOIN document_artifacts a USING(artifact_id)
            WHERE a.company_id=? AND a.artifact_type=? AND s.document_url=? ORDER BY s.artifact_source_id LIMIT 1''',
            (company_id,c.artifact_type,c.document_url)).fetchone()
        if row: prior[c.document_url]=dict(row)
    plan=DiscoveryPlan(symbol,company[0],Path('routine-no-manifest'),(page,),tuple(candidates))
    persisted=persist_discovery_plan(conn,plan,observed_at_utc=now,snapshot_root=snapshot_root,verified_company_id=company_id)
    revisions=[]
    for c in candidates:
        old=prior.get(c.document_url)
        if not old: continue
        if not conn.execute('SELECT 1 FROM artifact_contents WHERE artifact_id=?',(old['artifact_id'],)).fetchone(): continue
        evidence=json.loads(old['classification_evidence_json'])
        indicator={}
        if c.published_at_utc and c.published_at_utc!=evidence.get('published_at_utc'):
            indicator['publication']=c.published_at_utc
        if c.title_raw!=old['source_title_raw']:
            indicator['title']=c.title_raw
        if indicator:
            revisions.append(enqueue_revision(conn,artifact_id=old['artifact_id'],url=c.document_url,indicator=indicator,now=now))
    ratings=[aid for aid in persisted.artifact_ids if conn.execute('SELECT artifact_type FROM document_artifacts WHERE artifact_id=?',(aid,)).fetchone()[0]=='credit_rating']
    if ratings:
        from ...pipeline.credit_rating_downloads import queue_credit_rating_downloads
        queue_credit_rating_downloads(conn,artifact_ids=ratings,created_at_utc=now,include_queue_counts=False)
    return {'status':'discovered' if candidates else 'not_yet_published',**persisted.to_dict(),'revisions':revisions}


def refresh_official_pages(conn, *, company_ids, now, snapshot_root, session, request_limit=2):
    if not 0<=request_limit<=2: raise ValueError('Official page budget must be 0–2')
    results=[];requested=0
    for company_id in dict.fromkeys(company_ids):
        pages=company_pages(conn,company_id,permitted_only=False,now=now)
        visited=set()
        while pages and requested<request_limit:
            source,url=pages.pop(0)
            if url in visited: continue
            visited.add(url)
            if not allowed_page(conn,url):
                results.append({'company_id':company_id,'url':url,'status':'source_access_blocked','requests':0})
                continue
            requested+=1
            try:
                response=session.get(url,allow_redirects=False,stream=True,timeout=(10,20))
                try:
                    if response.status_code!=200:
                        results.append({'company_id':company_id,'url':url,'http_status':response.status_code,'availability_scope':'source_page',
                            'status':'source_access_blocked' if response.status_code in (401,403,429) else
                            'confirmed_unavailable' if response.status_code==410 else 'temporarily_unavailable'})
                        continue
                    body=bytearray()
                    for chunk in response.iter_content(chunk_size=65536):
                        body.extend(chunk)
                        if len(body)>2*1024*1024: raise ValueError('Official page byte budget exceeded')
                    if not any(t in response.headers.get('Content-Type','').lower() for t in ('html','json')):
                        raise ValueError('Official landing page must be HTML')
                    result=ingest_page(conn,company_id=company_id,source=source,url=url,body=bytes(body),now=now,snapshot_root=snapshot_root)
                    results.append({'company_id':company_id,'url':url,**result})
                    # A single investor/financial landing page, no recursive crawl.
                    if source=='company_ir' and len(visited)==1:
                        for a in BeautifulSoup(bytes(body),'html.parser').find_all('a',href=True):
                            link=urljoin(url,a['href']);label=(a.get_text(' ',strip=True)+' '+link).lower()
                            if any(term in label for term in ('investor','financial-results','financials')) and urlsplit(link).hostname==urlsplit(url).hostname and allowed_page(conn,link):
                                pages.insert(0,(source,link));break
                finally: response.close()
            except Exception as exc:
                from ...core.host_request_contracts import AdministrativeDefer
                results.append({'company_id':company_id,'url':url,
                    'status':'source_access_blocked' if isinstance(exc,AdministrativeDefer) else 'temporarily_unavailable',
                    'error':str(exc)})
    return {'requests':requested,'observations':results}


def revision_patrol(conn, *, now, limit=1):
    """At most one retained URL/week, within 180 days of source publication.

    Full validated GETs handle servers with absent/unreliable validators. This
    finite lane does not reset originals or request blocked publisher URLs.
    """
    from datetime import timedelta
    from ...documents.availability import parse_utc,format_utc
    if limit not in (0,1): raise ValueError('Revision patrol budget is zero or one')
    instant=parse_utc(now)
    cutoff=format_utc(instant-timedelta(days=180));due=format_utc(instant-timedelta(days=7))
    rows=conn.execute('''SELECT s.artifact_id,s.document_url,s.artifact_source_id FROM artifact_sources s
        JOIN document_artifacts a USING(artifact_id)
        WHERE a.artifact_type IN ('presentation','transcript','credit_rating')
          AND s.last_checked_at_utc<=? AND COALESCE(a.published_at_utc,a.first_observed_at_utc)>=?
          AND s.availability_status='available'
          AND EXISTS(SELECT 1 FROM artifact_contents c WHERE c.artifact_id=a.artifact_id)
          AND EXISTS(SELECT 1 FROM screener_collection_scope sc WHERE sc.company_id=a.company_id AND sc.eligibility='eligible')
        ORDER BY s.last_checked_at_utc,a.period_end DESC,s.artifact_source_id LIMIT ?''',(due,cutoff,limit)).fetchall()
    results=[]
    for row in rows:
        if not allowed_page(conn,row['document_url']): continue
        # The week is evidence of the finite check policy, never a revision claim.
        result=enqueue_revision(conn,artifact_id=row['artifact_id'],url=row['document_url'],
            indicator={'bounded_hash_check':instant.strftime('%G-W%V')},now=now)
        with conn:
            conn.execute('UPDATE artifact_sources SET last_checked_at_utc=? WHERE artifact_source_id=?',(now,row['artifact_source_id']))
        results.append({'artifact_id':row['artifact_id'],**result})
    return results


def announcement_page(conn, *, company_id, url, body, now):
    """Reuse the verified official parser, retaining its original JSON bytes."""
    import hashlib
    from html import escape
    from .nse_recovery import NSECorporateAnnouncementResolver
    from .recovery_common import RecoveryQuery
    params=parse_qs(urlsplit(url).query)
    symbol=params.get('symbol',[''])[0]
    query=RecoveryQuery('nse',url,'NSE',symbol,'','')
    observations=NSECorporateAnnouncementResolver().parse_response(body,query=query,discovered_at_utc=now,
        terms=('presentation','transcript','credit rating','rating rationale'))
    identities={r[0]:r[1] for r in conn.execute('SELECT identifier_type,identifier_value FROM security_identifiers WHERE company_id=? AND is_current=1',(company_id,))}
    if symbol!=identities.get('NSE'):raise ValueError('Announcement query company identity mismatch')
    anchors=[]
    for observation in observations:
        returned=observation.company_identifiers
        if not returned or not any(identities.get(kind) in values for kind,values in returned.items()):continue
        if any(kind in identities and identities[kind] not in values for kind,values in returned.items()):continue
        evidence=observation.source_evidence
        publication=observation.published_at_utc
        attrs=f'data-published-at-utc="{escape(publication)}"' if publication else ''
        identifier=evidence.get('announcement_id')
        if identifier:attrs+=f' data-exchange-announcement-id="{escape(str(identifier))}"'
        anchors.append(f'<a href="{escape(observation.candidate_url)}" {attrs}>{escape(observation.document_title or "")}</a>')
    page,candidates=_parse_page(symbol=f'company-{company_id}',page_position=0,source='nse',page_url=url,
        publisher_claim=None,content_path=Path('announcement.json'),content=''.join(anchors).encode())
    digest=hashlib.sha256(body).hexdigest()
    page=replace(page,content=body,content_sha256=digest)
    candidates=[replace(c,discovery_page_sha256=digest,identity_basis='verified_exchange_announcement_company_and_link') for c in candidates]
    return page,candidates
