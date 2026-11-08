"""Read-only diagnostic only, no canary execution/recovery."""
import importlib.util,json,subprocess
from pathlib import Path
OUT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('driver',OUT/'driver.py');d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
g=d.host.writer_gate();d.save('host-gate.json',g)
cmd=d.base()+['--mount','type=volume,src=trading-ai_scraper-data,dst=/data,readonly','--mount','type=bind,src='+str(OUT)+',dst=/proof,readonly','--env','SCRAPER_DOCUMENT_STORE_PATH=/data/snapshots','--entrypoint','python',d.host.IMAGE,'/proof/observe.py','diagnostic']
r=subprocess.run(cmd,capture_output=True,text=True);(OUT/'diagnostic-stdout.log').write_text(r.stdout);(OUT/'diagnostic-stderr.log').write_text(r.stderr)
if r.returncode:raise RuntimeError(r.stderr)
v=json.loads(r.stdout);d.save('diagnostic.json',v)
b=json.loads((OUT/'baseline075.json').read_text())['db_before'];print(json.dumps({'current_identity':v['db_before'],'expected_identity':b,'control_row':v['control_row'],'other_claim_keys':v['other_claim_keys'],'work_references':v['work_references'],'current_sha256':v['db_sha256'],'physical_identity_values_equal':all(v['db_before'][k]==b[k] for k in v['db_before'])},indent=2))
