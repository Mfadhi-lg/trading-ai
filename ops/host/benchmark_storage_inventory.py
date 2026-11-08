"""Compare full inventories under paused admissions; write only local evidence."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'ops/activation'),str(ROOT/'ops/host')]
import acquisition_lifecycle as lifecycle
from phase4_documents import STORE
from scraper_storage_growth import inventory as original

spec=importlib.util.spec_from_file_location('candidate_inventory','/tmp/scraper-throughput-20261102/scraper_storage_growth.py')
candidate=importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
out=ROOT/'artifacts/scraper-throughput-repair-20261102'
out.mkdir(exist_ok=True)
with lifecycle.lock(lifecycle.STATE/'operator.lock'),lifecycle.lock(
        lifecycle.ACTIVATION/'.scheduler-admission.lock'),lifecycle.lock(
        lifecycle.ACTIVATION/'.nse-owner.lock'):
    if not lifecycle.PAUSE.exists():raise RuntimeError('Paused admissions required')
    measurements=[]
    baseline=None
    for label,operation in [('parallel_first',lambda:candidate.inventory(STORE)),
                            ('original_serial',lambda:original(STORE)),
                            ('parallel_repeat',lambda:candidate.inventory(STORE))]:
        start=time.monotonic()
        files=operation()
        seconds=time.monotonic()-start
        if baseline is not None and files!=baseline:raise RuntimeError('Inventories differ')
        baseline=files
        measurement={'method':label,'seconds':round(seconds,3),'files':len(files)}
        measurements.append(measurement)
        lifecycle.atomic(out/'inventory-benchmark.json',{'status':'RUNNING','measurements':measurements})
        print(json.dumps(measurement),flush=True)
    lifecycle.atomic(out/'inventory-benchmark.json',{'status':'PASS','all_inventories_identical':True,
        'measurements':measurements,'cache_note':'Parallel first warms metadata for both subsequent runs'})
