# Exact shell rendering for review only; NOT authorized to run.
docker run --rm --pull never --no-healthcheck --network none --read-only --cap-drop ALL --security-opt no-new-privileges --cpus 0.5 --memory 1536m --tmpfs /tmp:rw,noexec,nosuid,size=32m --mount type=volume,src=trading-ai_scraper-data,dst=/data --mount type=bind,src=/Users/mohamedfadhi/Fadhi/trading-ai/docs/acquisition-readiness/baselines/AR-01.3C/approved-config/runtime-identity-core.json,dst=/run/trading-ai/runtime-identity.json,readonly --mount type=bind,src=/Users/mohamedfadhi/Fadhi/trading-ai/docs/acquisition-readiness/baselines/AR-01.3C/approved-config/store-sentinel.json,dst=/data/.trading_ai_runtime_identity.json,readonly --mount type=bind,src=/Users/mohamedfadhi/Fadhi/trading-ai/docs/acquisition-readiness/baselines/AR-01.3C/approved-config/core-documents-sentinel.json,dst=/data/snapshots/.trading_ai_runtime_identity.json,readonly --env TRADING_AI_ENV=production --env TRADING_AI_RUNTIME_IDENTITY_FILE=/run/trading-ai/runtime-identity.json --env SCRAPER_DB_PATH=/data/scraper.db --env SCRAPER_STORE_PATH=/data --env SCRAPER_DOCUMENT_STORE_PATH=/data/snapshots --entrypoint python trading-ai-c281-release@sha256:4d289faa67334bc870d4f2a6a181feecee777fd427eac209b9adef68ecab0238 -c '
import json, signal
from pathlib import Path
from scrapper.db.database import connect, check_schema_compatibility
from scrapper.db.document_work_claims import claim_extraction, assert_extraction_owned, release_extraction, utc_now
def deadline(*args):
    raise RuntimeError("canary deadline; stop and preserve state")
signal.signal(signal.SIGALRM, deadline)
signal.alarm(20)
key = "AR-01.3C/control-ownership/2026-10-08/v1"
conn = connect(Path("/data/scraper.db"))
try:
    check_schema_compatibility(conn)
    if conn.execute("SELECT 1 FROM document_extraction_claims WHERE resource_key=?", (key,)).fetchone() is not None:
        raise RuntimeError("existing control key; STOP without upsert/retry")
    if conn.execute("SELECT 1 FROM document_processing_work WHERE resource_key=?", (key,)).fetchone() is not None:
        raise RuntimeError("unexpected real work reference; STOP")
    before = conn.total_changes
    now = utc_now()
    print(json.dumps({"phase":"prepared","resource_key":key,"now":now,"lease_seconds":60}), flush=True)
    claim = claim_extraction(conn, key, seconds=60, now=now)
    print(json.dumps({"phase":"claimed","claim":claim}), flush=True)
    assert_extraction_owned(conn, claim)
    with conn:
        if release_extraction(conn, claim) != 1:
            raise RuntimeError("fenced release did not change exactly one row")
    final = dict(conn.execute("SELECT * FROM document_extraction_claims WHERE resource_key=?", (key,)).fetchone())
    if final["status"] != "RELEASED" or conn.total_changes-before != 2:
        raise RuntimeError("unexpected row/statement effect; STOP")
    print(json.dumps({"phase":"released","record":final,"affected_rows":2}), flush=True)
finally:
    conn.close()
'
