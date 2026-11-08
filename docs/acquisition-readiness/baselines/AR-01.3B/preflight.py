"""AR-01.3B read-only pre-write inspection, not migration."""
import importlib.util,json,hashlib,os,subprocess,sys
from pathlib import Path
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[3]
spec=importlib.util.spec_from_file_location('host',ROOT/'ops/docker/canary/ar013_readonly_host.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
g=h.writer_gate();g['harness_sha256']=hashlib.sha256((OUT/'inspect_db.py').read_bytes()).hexdigest()
(OUT/'host-gate.json').write_text(json.dumps(g,indent=2)+'\n')
args=['docker','run','--rm','--pull','never','--no-healthcheck','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--cpus','1','--memory','1536m','--tmpfs','/tmp:rw,noexec,nosuid,size=32m','--mount','type=volume,src=trading-ai_scraper-data,dst=/data,readonly','--mount','type=bind,src='+str(OUT)+',dst=/proof,readonly','--env','SCRAPER_DOCUMENT_STORE_PATH=/data/snapshots','--entrypoint','python',h.IMAGE,'/proof/inspect_db.py','pre']
(OUT/'pre-command.json').write_text(json.dumps(args,indent=2)+'\n')
r=subprocess.run(args,capture_output=True,text=True)
(OUT/'pre-stdout.log').write_text(r.stdout);(OUT/'pre-stderr.log').write_text(r.stderr)
if r.returncode:
 print(r.stderr);sys.exit(r.returncode)
d=json.loads(r.stdout);(OUT/'pre.json').write_text(json.dumps(d,indent=2)+'\n')
(OUT/'pre-writer-after.json').write_text(json.dumps(h.writer_gate(),indent=2)+'\n')
print(json.dumps({'status':d['status'],'schema_latest':d['schema_latest'],'source_sha256':d['source_sha256'],'pending':d['pending'],'all_init_directories_already_exist':all(d['required_directories'].values())},indent=2))
