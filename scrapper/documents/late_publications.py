"""Two finite long-tail checks, extending only exhausted missing-document waits."""
import json
from .availability import RetryPolicy, parse_utc

LONG_TAIL_HOURS=(2160,4320)  # day90 and day180; exhaustion remains unknown.


def extend_exhausted_waits(conn, *, now, limit=1):
    if type(limit) is not int or not 0 <= limit <= 2:
        raise ValueError('Long-tail extension is bounded to two expectations')
    rows=conn.execute('''SELECT e.* FROM artifact_expectations e
        WHERE e.artifact_type IN ('presentation','transcript')
          AND e.terminal_reason='retry_schedule_exhausted' AND e.claim_token IS NULL
          AND e.availability_status IN ('expected_not_yet_published','discovery_failed','inaccessible')
          AND json_extract(e.schedule_json,'$.long_tail_v1') IS NULL
          AND EXISTS (SELECT 1 FROM screener_collection_scope s WHERE s.company_id=e.company_id AND s.eligibility='eligible')
        ORDER BY e.expected_after_utc DESC,e.expectation_id LIMIT ?''',(limit,)).fetchall()
    extended=[]
    for row in rows:
        policy=json.loads(row['schedule_json'])
        if max(policy['offsets_hours'])>=LONG_TAIL_HOURS[0]:
            continue
        # Do not resurrect a years-old missing link. New official publications
        # can still enter discovery independently of this result wait window.
        if (parse_utc(now)-parse_utc(row['expected_after_utc'])).total_seconds()>180*86400:
            continue
        policy['offsets_hours']+=list(LONG_TAIL_HOURS)
        policy['long_tail_v1']=True
        next_check=RetryPolicy(tuple(policy['offsets_hours']),policy['unknown_retry_limit'],policy['claim_lease_seconds']).next_after_result(
            expected_after_utc=row['expected_after_utc'],completed_attempt_count=row['attempt_count'],
            status=row['availability_status'],retryable=True,checked_at_utc=now)[0]
        with conn:
            changed=conn.execute('''UPDATE artifact_expectations SET schedule_json=?, next_check_at_utc=?,
                terminal_reason=NULL,updated_at_utc=? WHERE expectation_id=? AND claim_token IS NULL
                AND terminal_reason='retry_schedule_exhausted' ''',
                (json.dumps(policy,sort_keys=True),next_check,now,row['expectation_id'])).rowcount
        if changed: extended.append(row['expectation_id'])
    return extended
