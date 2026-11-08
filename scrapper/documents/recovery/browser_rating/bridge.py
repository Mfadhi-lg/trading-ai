"""Browser bridge dispatch; ledger and canonical persistence retain ownership."""
from scrapper.core.attempts import claim_attempt
import base64
import json
import secrets
from scrapper.db.documents.recovery import browser_rating_bridge as repository
from .protocol import validate_request, response

def serve(conn, args, *, lines, emit, worker_state, release_owned_jobs, claim,
          cool_down, finish, validate_report, company_names, import_report,
          DownloadPolicy, DownloadResult, utc_now, renewal_factory=None, observe=None, shared_permits=False, authorization=None):
    """JSON-lines bridge for the visible host browser and the container DB."""
    owner = f'headed-browser:{args.run_id}:{secrets.token_hex(16)}'
    renewals = {}
    claims = {}
    generations = {}
    started = False
    broker = None
    if shared_permits:
        from .host_permit_bridge import Broker
        broker = Broker(conn, args.run_id, authorization)
    try:
        for line in lines:
            try:
                request = json.loads(line)
                validate_request(request)
                response_emit = lambda value: emit(response(value, request))
                action = request['action']
                if action == 'shared_setup':
                    if broker is None: raise ValueError('Shared browser bridge is not enabled')
                    response_emit(broker.setup())
                    continue
                if action.startswith('host_') and broker is None:
                    raise ValueError('Shared browser bridge is not enabled')
                if observe is not None: observe('READY')
                if action == 'release':
                    for renewal in renewals.values():
                        if renewal is not None: renewal.__exit__()
                    renewals.clear()
                    response_emit({'released': release_owned_jobs(conn, args.run_id, owner)})
                    continue
                started = True
                worker_state(conn, args.run_id, owner, 'DOWNLOADING')
                if action == 'claim':
                    if broker is not None: broker.setup()
                    job = claim(conn, args.run_id, ['www.icra.in'], owner, request.get('job_ids', ()))
                    if job:
                        key = job['browser_job_id']
                        generations[key] = generations.get(key, 0) + 1
                        claims[key] = dict(job)
                        if key in renewals:
                            previous = renewals.pop(key)
                            if previous is not None: previous.__exit__()
                        renewals[key] = renewal_factory(conn, job).__enter__() if renewal_factory else None
                    if observe is not None: observe('WORKING' if job else 'WAITING')
                    cooldown = repository.cooldown(conn, args.run_id, utc_now())
                    worker_state(conn, args.run_id, owner, 'DOWNLOADING' if job else 'WAITING')
                    response_emit({'job': dict(job) if job else None, 'cooldown_until_utc': cooldown[0] if cooldown else None})
                    continue
                key = request['browser_job_id']
                job = claims.get(key)
                current = repository.owned_job(conn, key, args.run_id, owner)
                supplied = request.get('claim_attempt_count')
                supplied_token = request.get('claim_token')
                # Legacy messages remain usable on the first claim only. Reclaimed
                # work requires an explicit generation; never infer it from the DB.
                owned = (
                    job is not None and current is not None
                    and int(current['attempt_count']) == claim_attempt(job, 'browser')
                    and current['claim_token'] == job['claim_token']
                )
                if not owned:
                    raise ValueError('Bridge job is not owned by this run')
                if (
                    (supplied_token is not None and supplied_token != job['claim_token'])
                    or (supplied_token is None and generations[key] > 1)
                    or (supplied is not None and (
                        type(supplied) is not int or supplied != claim_attempt(job, 'browser')
                    ))
                ):
                    raise ValueError('Bridge job is not owned by this run')
                if renewals[key] is not None: renewals[key].check(conn)
                if broker is not None:
                    from ....db.browser_host_defer import assert_unexpired
                    assert_unexpired(current, utc_now())
                if action.startswith('host_'):
                    response_emit(broker.handle(request,job))
                    if action == 'host_defer':
                        renewal = renewals.pop(key)
                        if renewal is not None: renewal.__exit__()
                    continue
                if broker is not None: broker.assert_drained(job)
                if action == 'rate_limit':
                    until = cool_down(conn, args.run_id, 'www.icra.in', request.get('retry_after'))
                    finish(conn, job, 'PENDING', error='Publisher HTTP 429; waiting for cooldown', evidence={'cooldown_until_utc': until})
                    response_emit({'status': 'PENDING', 'cooldown_until_utc': until})
                elif action == 'import':
                    body = base64.b64decode(request['body_base64'], validate=True)
                    if not 0 < len(body) <= DownloadPolicy().max_bytes:
                        raise ValueError('Bridge body size is invalid')
                    evidence = request.get('evidence', {})
                    evidence.update(validate_report(body, 'application/pdf', request['final_url'],
                        job['company_name'], company_names(conn, job['artifact_id'])))
                    result = DownloadResult(requested_url=job['requested_url'], final_url=request['final_url'],
                        http_status=request.get('http_status'), response_headers=request.get('response_headers', {}),
                        redirects=(), declared_media_type='application/pdf', detected_media_type='application/pdf',
                        body=body, byte_size=len(body), result_status='validated', elapsed_ms=request.get('elapsed_ms', 0))
                    status, content_id, evidence = import_report(conn, job, result, evidence, args.run_id)
                    finish(conn, job, status, content_id, result.final_url, evidence=evidence)
                    response_emit({'status': status, 'artifact_id': job['artifact_id'], 'content_id': content_id, 'byte_size': len(body)})
                elif action == 'finish':
                    status = request['status']
                    if status not in ('FAILED', 'REVIEW_REQUIRED', 'SKIPPED_EXISTING', 'PENDING'):
                        raise ValueError('Invalid bridge completion status')
                    existing = repository.existing_content(conn, job['artifact_id'])
                    finish(conn, job, status, content_id=existing[0] if existing else None,
                        error=request.get('error'), evidence=request.get('evidence'))
                    response_emit({'status': status, 'artifact_id': job['artifact_id']})
                else:
                    raise ValueError('Unknown bridge action')
                renewal = renewals.pop(key)
                if renewal is not None: renewal.__exit__()
            except Exception as error:
                emit({'error': f'{type(error).__name__}: {error}'})

    finally:
        for renewal in renewals.values():
            if renewal is not None: renewal.__exit__()
        if started:
            release_owned_jobs(conn, args.run_id, owner)
            worker_state(conn, args.run_id, owner, 'STOPPED')
