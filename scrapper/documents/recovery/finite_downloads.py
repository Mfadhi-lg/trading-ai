"""Exact finite historical scope, separate from routine historical allowance."""
from ...core.host_policy import canonical_host, policy_for
from ..availability import parse_utc


def validate_campaign(conn, campaign, *, now):
    if campaign.get('version')!=1 or not campaign.get('campaign_id'):
        raise ValueError('Unknown finite download campaign')
    if parse_utc(now)>=parse_utc(campaign['expires_at_utc']):raise ValueError('Campaign expired')
    jobs=campaign['jobs']
    if not 1<=len(jobs)<=10 or campaign.get('max_requests')!=len(jobs):
        raise ValueError('Campaign must contain 1–10 exact requests')
    if campaign.get('max_response_bytes')!=10485760 or campaign.get('max_redirects')!=0:
        raise ValueError('Campaign transport limits changed')
    ids=[];urls=set();artifacts=set()
    for selected in jobs:
        row=conn.execute('SELECT * FROM artifact_download_queue q JOIN document_artifacts a USING(artifact_id) WHERE q.download_job_id=?',
                         (selected['download_job_id'],)).fetchone()
        if row is None or any(row[k]!=selected[k] for k in
            ('download_job_id','artifact_id','company_id','requested_url','target_host','period_end')):
            raise ValueError('Canonical campaign URL/company/period identity changed')
        if canonical_host(row['requested_url'])!=row['target_host'] or policy_for(row['requested_url']).blocked:
            raise ValueError('Campaign publisher/URL not permitted')
        if row['download_job_id'] in ids or row['requested_url'] in urls or row['artifact_id'] in artifacts:
            raise ValueError('Campaign contains duplicate work')
        ids.append(row['download_job_id']);urls.add(row['requested_url']);artifacts.add(row['artifact_id'])
    return tuple(ids)
