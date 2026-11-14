"""Bounded immutable explorer exports with a local self-refreshing entry point."""
from datetime import date
import fcntl
import hashlib
import json
import os
from pathlib import Path

from experiments.historical_candle_archive_v1.storage import atomic, dump
from .planner import aware, digest


SHELL = '''<!doctype html><meta charset="utf-8"><title>Current index explorer</title>
<style>html,body,iframe{margin:0;width:100%;height:100%;border:0}body{background:#10131a;color:white}</style>
<iframe id="explorer" title="Newest validated index explorer"></iframe>
<script>
let selected='';
function refresh(){const script=document.createElement('script');script.src='latest.js?at='+Date.now();
script.onload=()=>{const current=window.INDEX_LATEST;if(current&&current.entry!==selected){
selected=current.entry;document.getElementById('explorer').src=selected;}script.remove();};
script.onerror=()=>script.remove();document.head.appendChild(script);}
refresh();setInterval(refresh,30000);
</script>'''


def archive_snapshot(store, start, end):
    """Metadata-only source revision key; no provider or candle-file scans."""
    return [list(row) for row in store.db.execute('''SELECT dataset_id,parquet_sha256,raw_sha256,
        observed_at,completeness FROM datasets WHERE substr(first_start,1,10)<=?
        AND substr(last_start,1,10)>=? ORDER BY dataset_id''',(end,start)).fetchall()]


def _verified_build(base):
    manifest=base/'SHA256.json'
    if not manifest.is_file():
        raise ValueError('Retained explorer build is incomplete')
    files=json.loads(manifest.read_text())
    if 'charts/index.html' not in files:
        raise ValueError('Explorer entry missing from sealed build')
    for relative, expected in files.items():
        path=(base/relative).resolve()
        if not path.is_relative_to(base.resolve()) or not path.is_file():
            raise ValueError('Invalid explorer seal path')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('Retained explorer checksum mismatch')


def publish(config, snapshot, *, calendar, now, end, incremental_state, builder=None):
    """Publish only after the maintained builder completes and the seal verifies.

    Failed attempts remain retained; at most three are admitted per source scope.
    All exports preserve existing provisional/partial index warnings. The local
    index.html polls a classic local script, so file:// browsing needs no fetch
    permissions or network server. It reloads its frame when a new build appears.
    """
    root=Path(config['index_export_output'])
    if not root.is_absolute():
        raise ValueError('Explicit absolute index_export_output required')
    start=config.get('index_start') or config.get('derived_start')
    if not start or not 0 <= (date.fromisoformat(end)-date.fromisoformat(start)).days <= 731:
        raise ValueError('Explicit bounded index window of at most 732 calendar days required')
    intraday_start=config.get('index_intraday_start',start)
    if not start <= intraday_start <= end:
        raise ValueError('Intraday explorer window must be inside approved daily window')
    scope=dict(version='LIVE-FRESH-explorer-v1',snapshot=snapshot,start=start,end=end,
               intraday_start=intraday_start,calendar_sha256=config['calendar_sha256'],
               action_review_sha256=(hashlib.sha256(Path(config['index_action_review']).read_bytes()).hexdigest()
                                      if config.get('index_action_review') else None))
    ident=digest(scope)
    root.mkdir(parents=True,exist_ok=True)
    with (root/'.index-refresh.lock').open('a+b') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(status='BLOCKED',reason='INDEX_EXPORT_BUSY')
        final=root/'builds'/ident
        old=json.loads((root/'latest.json').read_text()) if (root/'latest.json').exists() else {}
        if final.exists():
            _verified_build(final)
            if old.get('build')==ident and (root/'index.html').is_file() and (root/'latest.js').is_file():
                return dict(status='UNCHANGED',build=ident,entry=str(root/'index.html'))
        else:
            attempts=root/'attempts'/ident
            attempts.mkdir(parents=True,exist_ok=True)
            count=len(list(attempts.iterdir()))
            if count>=3:
                return dict(status='BLOCKED',reason='INDEX_BUILD_ATTEMPTS_EXHAUSTED',source_digest=ident)
            attempt=attempts/f'{count+1:04d}'
            if builder is None:
                from scrapper.indices.refresh import build_export
                builder=build_export
            entry=Path(builder(attempt,start=start,end=end,intraday_start=intraday_start,
                action_review=config.get('index_action_review'),incremental_state=incremental_state,
                reviewed_calendar=calendar))
            if entry.resolve()!=(attempt/'charts/index.html').resolve():
                raise ValueError('Maintained explorer returned unexpected entry')
            dump(attempt/'LIVE_FRESH_SOURCE_SCOPE.json',scope)
            from scrapper.indices.refresh import seal
            seal(attempt);_verified_build(attempt)
            final.parent.mkdir(parents=True,exist_ok=True)
            os.replace(attempt,final)
        if old.get('published_at') and aware(old['published_at'])>aware(now):
            return dict(status='BLOCKED',reason='NEWER_EXPLORER_ALREADY_PUBLISHED',build=ident)
        pointer=dict(build=ident,entry=f'builds/{ident}/charts/index.html',published_at=aware(now).isoformat(),
                     status='PROVISIONAL;SEE_PER_SERIES_COVERAGE',source_digest=ident,start=start,end=end)
        atomic(root/'index.html',SHELL.encode())
        atomic(root/'latest.js',('window.INDEX_LATEST='+json.dumps(pointer,sort_keys=True).replace('<','\\u003c')+';\n').encode())
        dump(root/'latest.json',pointer)
        return dict(status='PUBLISHED',build=ident,entry=str(root/'index.html'))
