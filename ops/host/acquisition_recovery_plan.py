"""Read-only failure taxonomy and bounded recovery plan from a guarded census."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import re

CATEGORIES=('network_connection','network_timeout','http_403','http_429','http_5xx','publisher_restriction',
    'unavailable_or_unpublished','expired_link','dns_tls','invalid_response','parsing',
    'identity_mismatch','mapping_gap','database_contention','lease_ownership',
    'storage_unavailable','storage_capacity','docker','scheduler','execution_deadline',
    'configuration_drift','integrity_failure','permanent_failure','policy_exclusion','unknown')


def classify(text):
    t=text.lower()
    for category,patterns in (
        ('policy_exclusion',('below screener market-cap','not above screener market-cap','below_floor','skipped_policy','standalone_only_policy')),
        ('integrity_failure',('hash changed','identity changed','database disk image is malformed','not authorized','checksum')),
        ('identity_mismatch',('identity mismatch','company mismatch','wrong company')),
        ('publisher_restriction',('publisher_terms','blocked_terms','access_blocked','robots','captcha','interaction_required')),
        ('http_403',('http_403','status=403','http 403')),('http_429',('http_429','status=429','http 429')),('http_5xx',('http_5','status=5','http 5')),
        ('dns_tls',('ssl','tls','certificate','dns','name resolution','nameresolutionerror','failed to resolve','name or service not known')),
        ('network_timeout',('timeout','timed_out','timed out')),
        ('network_connection',('connectionerror','connection aborted','remotedisconnected','connection refused','connection reset')),
        ('expired_link',('expired link','expired url','http_410')),
        ('unavailable_or_unpublished',('404','http_404','not_published','unavailable_slot','no_validated_report_target')),
        ('database_contention',('database is locked','database is busy')),
        ('lease_ownership',('leased','ownershiplost','claim lost','lease expired')),
        ('storage_capacity',('capacity','no space','reserve')),
        ('storage_unavailable',('not mounted','store does not exist','no such device')),
        ('docker',('docker',)),('scheduler',('scheduler',)),
        ('execution_deadline',('deadline',)),('configuration_drift',('drift','config changed')),
        ('mapping_gap',('mapping','identifier','unresolved')),
        ('invalid_response',('unexpected_html','unknown_signature','media_type','invalid_response')),
        ('parsing',('headers are missing','parsing','parse artifact','manifest_coverage','parser')),
        ('permanent_failure',('gone','unsupported','too_large','rejected_artifact_type'))):
        if any(p in t for p in patterns):return category
    return 'unknown'


def plan(snapshot):
    groups={}
    for lane,key in (('financial','financial_failures'),('documents','document_failures'),('ratings','rating_failures')):
        for row in snapshot.get(key,[]):
            text=row.get('last_error_json') or ' '.join(str(row.get(k) or '') for k in ('status','last_result_status','last_error'))
            category=classify(text)
            if row['status']=='SKIPPED_POLICY':category='policy_exclusion'
            signature=re.sub(r'\b\d{4,}\b','<id>',text)
            if category=='policy_exclusion':signature='Retained coverage or publisher policy exclusion'
            if lane=='financial':
                try:
                    error=json.loads(text).get('error',{})
                    signature=error.get('type','')+': '+error.get('message',text)
                except (ValueError,AttributeError):pass
            signature=re.sub(r'https?://\S+','<url>',signature)
            if category=='dns_tls':signature='DNS resolution failure' if any(x in text.lower() for x in ('nameresolutionerror','failed to resolve','name resolution','name or service not known')) else 'TLS/certificate failure'
            elif category=='network_connection':signature='Connection reset/refused/closed by transport'
            elif category=='network_timeout':signature='HTTP network timeout'
            elif category in ('http_403','http_429','http_5xx'):
                status=re.search(r'(?:http_|status=|http )([45]\d\d)',text.lower())
                signature='HTTP '+(status.group(1) if status else category)
            elif category=='unknown':signature=re.sub(r"(?:url: |host=)[^)]*",'<source>',signature)

            group=groups.setdefault((lane,category,signature),{'lane':lane,'category':category,'signature':signature,
                'jobs':0,'companies':set(),'unresolved_company_paths':set(),'sources':set(),'periods':set(),
                'first_observed':None,'latest_observed':None,'sample_job_ids':[], 'states':defaultdict(int)})
            group['jobs']+=1;group['states'][row['status']]+=1
            if row.get('company_id') is not None:group['companies'].add(row['company_id'])
            elif row.get('company_path_normalized'):group['unresolved_company_paths'].add(row['company_path_normalized'])
            group['sources'].add(row.get('target_host') or ('screener' if lane=='financial' else 'rating resolver'))
            group['periods'].add(row.get('result_period') or row.get('period_end') or 'unknown')
            for field,key,fn in (('first_observed','created_at_utc',min),('latest_observed','updated_at_utc',max)):
                value=row.get('first_error_at_utc' if field=='first_observed' else 'last_error_at_utc') or row.get(key)
                if value:group[field]=fn(group[field],value) if group[field] else value
            if len(group['sample_job_ids'])<5:group['sample_job_ids'].append(row.get('queue_item_id',row.get('download_job_id')))
    output=[]
    for g in groups.values():
        g['distinct_companies']=len(g['companies']);g.pop('companies')
        g['unresolved_distinct_paths']=len(g.pop('unresolved_company_paths'))
        g['sources']=sorted(g['sources']);g['periods']=sorted(g['periods']);g['states']=dict(g['states'])
        transient=g['category'] in ('network_connection','network_timeout','http_5xx','dns_tls','database_contention','lease_ownership')
        g['retry_eligibility']='Conditional: existing retry state, due time, source policy, identity, attempts and budget must all pass' if transient else 'No automatic terminal reopening'
        g['action']='Use existing canonical claim/backoff at a later eligible slot; preserve attempts' if transient else 'Retain exclusion' if g['category']=='policy_exclusion' else 'Inspect retained evidence; require a verified changed input before any terminal recovery'
        output.append(g)
    output.sort(key=lambda g:(-g['jobs'],g['lane'],g['category']))
    return {'observed_at_utc':snapshot.get('observed_at_utc'),'source_requests':0,'production_writes':0,
        'groups':output,'categories':{lane:{category:sum(g['jobs'] for g in output if g['lane']==lane and g['category']==category)
            for category in CATEGORIES} for lane in ('financial','documents','ratings')},
        'scope':snapshot.get('scope'),
        'download_selection':snapshot.get('eligible_download_selection'),
        'processing_selection':snapshot.get('eligible_processing_selection'),
        'terminal_jobs_reopened':0,
        'eta':'Requires measured productive throughput for eligible work; total pending divided by nominal slots is only a policy ceiling estimate'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.write_text(json.dumps(plan(json.loads(args.snapshot.read_text())),indent=2)+'\n')
