"""A supervised processing consumer alongside the existing bounded downloader."""
from contextlib import nullcontext
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def consume_queue(run_one, producer_done, *, limit, wait=time.sleep, clock=time.monotonic, timeout=420):
    """Claim saved inbox items one at a time, including arrivals during download."""
    if type(limit) is not int or not 1 <= limit <= 30:
        raise ValueError('Processing pipeline limit must be 1–30')
    from acquisition_failures import classify_exception, ENVIRONMENT
    selected, outcomes = [], []
    period_recovery = {'checked': 0, 'recovered': 0, 'completed_sweeps': 0}
    deadline = clock()+timeout
    while len(selected) < limit and clock() < deadline:
        # Read completion BEFORE selection so the final query sees all commits
        # made before the producer finished. Never exit on an earlier empty read.
        finished = producer_done()
        try:
            result = run_one()
        except Exception as exc:
            if classify_exception(exc)[1] != ENVIRONMENT:
                raise
            if finished:
                return {'status': 'DEFERRED_ENVIRONMENT', 'selected': selected,
                        'outcomes': outcomes, 'error': str(exc), 'period_recovery': period_recovery}
            wait(1)
            continue
        selected.extend(result.get('selected', []))
        outcomes.extend(result.get('outcomes', []))
        recovery = result.get('period_recovery', {})
        period_recovery['checked'] += recovery.get('checked', 0)
        period_recovery['recovered'] += recovery.get('recovered', 0)
        period_recovery['completed_sweeps'] += int(recovery.get('sweep_complete', False))
        if not result.get('selected'):
            if finished:
                break
            wait(1)
    return {'status': 'recorded' if outcomes else 'idle',
            'selected': selected, 'outcomes': outcomes, 'period_recovery': period_recovery}


class ProcessingCompanion:
    def __init__(self, command, *, log_path, result_path, done_path, env=None, timeout=450):
        self.command = command
        self.log_path, self.result_path, self.done_path = map(Path, (log_path, result_path, done_path))
        self.env, self.timeout = env, timeout
        self.process = None

    def __enter__(self):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if self.result_path.exists() or self.done_path.exists():
            raise RuntimeError('Processing companion was already started')
        self.log = self.log_path.open('w')
        self.started = time.monotonic()
        try:
            self.process = subprocess.Popen(self.command, stdout=self.log, stderr=subprocess.STDOUT,
                                            env=self.env, start_new_session=True)
        except BaseException:
            self.log.close()
            raise
        print(json.dumps({'stage': 'processing_pipeline_started',
                          'at': datetime.now(timezone.utc).isoformat(), 'pid': self.process.pid}), flush=True)
        return self

    def stop(self):
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                self.process.wait()
                return
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()

    def __exit__(self, kind, error, tb):
        if kind is not None:
            try:
                self.stop()
            finally:
                self.log.close()
            return False
        self.done_path.touch(exist_ok=False)
        print(json.dumps({'stage': 'pipeline_download_finished',
                          'at': datetime.now(timezone.utc).isoformat()}), flush=True)
        timed_out = False
        try:
            code = self.process.wait(timeout=max(1, self.timeout-(time.monotonic()-self.started)))
        except subprocess.TimeoutExpired:
            timed_out = True
            self.stop()
            code = 75
        finally:
            self.log.close()
        if self.result_path.exists() and code == 0:
            record = json.loads(self.result_path.read_text())
        else:
            errors = []
            selected, outcomes = [], []
            for line in self.log_path.read_text().splitlines():
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict) and item.get('stage') == 'execution_failure':
                    errors.append(item)
                if isinstance(item, dict) and item.get('stage') == 'processing_queue_item':
                    selected.extend(item.get('selected', []))
                    outcomes.extend(item.get('outcomes', []))
            record = {'stage': 'document_stage_outcome', 'document_stage': 'DOCUMENT_PROCESSING',
                      'companion': True, 'finished_at_utc': datetime.now(timezone.utc).isoformat(),
                      'result': {'status': 'DEFERRED_ENVIRONMENT' if code == 75 else 'STOP',
                                 'error': ('Processing companion deadline exceeded' if timed_out else
                                           errors[-1]['error'] if errors else f'Processing companion exited with code {code}; inspect '+str(self.log_path)),
                                 'outcomes': outcomes, 'selected': selected}}
        # The host records processing failures against the processing lane.
        # Completed downloads keep their own outcome and durable queue entries.
        record['process_exit_code'] = code
        self.record = record
        print(json.dumps(record), flush=True)
        return False


def begin(plan, output):
    config = plan.get('concurrent_processing')
    if not config:
        return nullcontext()
    if type(config['limit']) is not int or not 1 <= config['limit'] <= 30:
        raise ValueError('Invalid concurrent processing scope')
    output = Path(output)/'processing-pipeline'
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(DOCUMENT_PIPELINE_PARENT_PID=str(os.getpid()), DOCUMENT_PIPELINE_OUTPUT=str(output),
               PYTHONPATH=('/document_app' if plan.get('paired_downloads') else '/app')+':/activation')
    return ProcessingCompanion([sys.executable, '/activation/processing_pipeline.py'],
        log_path=output/'worker.log', result_path=output/'result.json', done_path=output/'download-finished',
        env=env)


def main():
    import ctypes
    import hashlib
    parent = int(os.environ['DOCUMENT_PIPELINE_PARENT_PID'])
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
        raise RuntimeError('Processing supervisor ownership lost')
    plan = json.loads(Path('/selected/plan.json').read_text())
    manifest = json.loads(Path('/selected/manifest.json').read_text())
    if hashlib.sha256(Path('/selected/plan.json').read_bytes()).hexdigest() != manifest['plan_sha256']:
        raise RuntimeError('Processing pipeline plan drift')
    config = plan['concurrent_processing']
    if not (plan.get('authorized') and plan.get('stage') == 'DOCUMENT_DOWNLOAD'
            or plan.get('scheduled') and plan.get('paired_downloads')):
        raise RuntimeError('Unauthorized concurrent processing')
    from scrapper.db.database import connect
    from scrapper.documents.ocr import TesseractOcr
    from scrapper.operations.workers.document_processing import run_automatic_processing
    from phase4_routine_runtime import routine_authorizer
    from acquisition_failures import ExecutionDeadline
    db = Path('/data/scraper.db')
    def guarded(path):
        if Path(path) != db:
            raise RuntimeError('Unexpected processing DB target')
        conn = connect(path)
        conn.set_authorizer(routine_authorizer)
        return conn
    def deadline(*_):
        raise ExecutionDeadline('Processing companion deadline exceeded')
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(430)
    output = Path(os.environ['DOCUMENT_PIPELINE_OUTPUT'])
    started = datetime.now(timezone.utc).isoformat()
    engine = TesseractOcr()
    def run_one():
        result = run_automatic_processing(db_path=db, epoch=config['epoch'], limit=1, historical_limit=0,
            ocr_engine=engine, output_dir=output/'extractions', connection_factory=guarded,
            focus_company_ids=config.get('focus_company_ids', []))
        if result.get('selected'):
            print(json.dumps({'stage': 'processing_queue_item', 'at': datetime.now(timezone.utc).isoformat(),
                              'selected': result['selected'], 'outcomes': result['outcomes']}), flush=True)
        return result
    result = consume_queue(run_one, (output/'download-finished').exists, limit=config['limit'])
    record = {'stage': 'document_stage_outcome', 'document_stage': 'DOCUMENT_PROCESSING',
              'companion': True, 'started_at_utc': started, 'finished_at_utc': datetime.now(timezone.utc).isoformat(),
              'result': result}
    temporary = output/'result.tmp'
    temporary.write_text(json.dumps(record)+'\n')
    os.replace(temporary, output/'result.json')
    print(json.dumps(record), flush=True)


if __name__ == '__main__':
    from acquisition_failures import runtime_entrypoint
    raise SystemExit(runtime_entrypoint(main))
