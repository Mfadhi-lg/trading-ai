"""One supervised download child alongside a bounded financial worker."""
from pathlib import Path
import json,os,subprocess,sys,time


class DownloadCompanion:
    def __init__(self, command, *, log_path, result_path, env=None, timeout=480):
        self.command=command;self.log_path=Path(log_path);self.result_path=Path(result_path)
        self.env=env;self.timeout=timeout;self.process=None
    def __enter__(self):
        self.log_path.parent.mkdir(parents=True,exist_ok=True)
        if self.result_path.exists():raise RuntimeError('Paired download result already exists; no replay')
        self.log=self.log_path.open('w');self.started=time.monotonic()
        try:self.process=subprocess.Popen(self.command,stdout=self.log,stderr=subprocess.STDOUT,env=self.env,cwd='/tmp')
        except BaseException:self.log.close();raise
        return self
    def __exit__(self,kind,error,tb):
        if kind is not None:
            self.process.terminate()
            try:self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
            self.log.close();return False
        try:
            code=self.process.wait(timeout=max(1,self.timeout-(time.monotonic()-self.started)))
        except subprocess.TimeoutExpired:
            self.process.kill();self.process.wait();raise BlockingIOError('Paired download deadline exceeded')
        finally:self.log.close()
        from acquisition_failures import ENVIRONMENT
        if code==ENVIRONMENT:
            raise BlockingIOError('Paired download environment deferred; inspect '+str(self.log_path))
        if code!=0 or not self.result_path.exists():
            raise RuntimeError('Paired download infrastructure failure; inspect '+str(self.log_path))
        record=json.loads(self.result_path.read_text())
        print(json.dumps(record),flush=True)
        if record.get('processing_companion'):
            print(json.dumps(record['processing_companion']),flush=True)
        return False



def document_environment(environment):
    result=dict(environment)
    if result.get('TRADING_AI_DB_BACKEND')=='postgresql':
        for source,destination in (
            ('TRADING_AI_DOCUMENTS_DATABASE_URL','DATABASE_URL'),
            ('TRADING_AI_DOCUMENTS_PGPASSFILE','PGPASSFILE'),
            ('TRADING_AI_DOCUMENTS_RUNTIME_IDENTITY_FILE','TRADING_AI_RUNTIME_IDENTITY_FILE')):
            if not result.get(source):raise RuntimeError('Native document companion target is missing')
            result[destination]=result.pop(source)
    return result


def begin(plan):
    pair=plan['paired_downloads']
    if not 1<=pair['limit']<=30:raise ValueError('Paired downloads require 1–30 jobs')
    root=Path('/data/exports/paired-downloads');key=pair['id']
    if not key.isalnum():raise ValueError('Invalid paired identity')
    env=document_environment(os.environ);env['PYTHONPATH']='/document_app:/activation'
    env['PAIRED_PARENT_PID']=str(os.getpid())
    return DownloadCompanion([sys.executable,'/activation/paired_download_runtime.py'],
        log_path=root/(key+'.log'),result_path=root/(key+'.json'),env=env,timeout=480)
