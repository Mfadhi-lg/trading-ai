"""Compact review bundle against retained untracked TECH-05/06 baseline; no publication."""
import difflib, hashlib, json, subprocess, zipfile
from pathlib import Path
from .repair import ROOT, OUT, N, A, sha, read, save

SOURCE_ROOT=Path('/Users/mohamedfadhi/Fadhi/trading-ai')

def main():
    frozen=json.loads((OUT/'evidence/BASELINE_HASHES.json').read_text())
    changes=[];diff=[]
    for path,digest in frozen.items():
        current=ROOT/path
        if sha(current)==digest:continue
        original=SOURCE_ROOT/path
        assert sha(original)==digest, 'Concurrent source baseline changed; do not package or overwrite: '+path
        changes.append(dict(path=path,before_sha256=digest,after_sha256=sha(current)))
        diff.extend(difflib.unified_diff(original.read_text().splitlines(keepends=True),current.read_text().splitlines(keepends=True),fromfile='a/'+path,tofile='b/'+path))
    assert len(changes)==7
    (OUT/'TECH-FIX-01.patch').write_text(''.join(diff))
    save('PATCH_MANIFEST.json',dict(task='TECH-FIX-01',base_commit='0e6ee264',branch='codex/tech-fix-01',
        baseline_note='TECH research was untracked in source checkout. Before hashes identify the exact retained baseline; this patch changes seven existing files. New TECH-FIX module and artifacts are supplied separately.',
        changed_existing_files=changes,new_source_directory='experiments/technical_fix_v1',patch_sha256=sha(OUT/'TECH-FIX-01.patch')))
    save('evidence/ISOLATION.json',dict(source_checkout=str(SOURCE_ROOT),worktree=str(ROOT),branch='codex/tech-fix-01',
        base_commit='0e6ee264',source_checkout_dirty_at_start=True,
        source_existing_changed_files_still_match_before_hashes=all(sha(SOURCE_ROOT/r['path'])==r['before_sha256'] for r in changes),
        protected_data_policy='No Passport/HCD files, databases, schemas, migrations, acquisition settings or manifests modified. Only isolated presentation files, source templates and TECH-FIX outputs changed.',
        authenticated_requests=0,credentials_read=False,research_databases_opened=0))
    (OUT/'README.md').write_text('''# TECH-FIX-01 review

Start with TECHNICAL_ACCEPTANCE_STATUS.md and DEFECT_RESOLUTION.csv. Current charts live in ../technical-integration-v2/charts/ in the isolated worktree. The source checkout is untouched.

TECH-FIX-01.patch changes exactly seven retained presentation/source files. PATCH_MANIFEST.json contains exact before/after hashes. New implementation/tests are in experiments/technical_fix_v1. Previous technical work was untracked at the Git base, so do not blindly overwrite or merge concurrent work. Apply the patch only to a checkout whose seven before hashes match, with the original retained research inputs available.

Reproduce with the commands in REGRESSION_TEST_RESULTS.md. No authorization/token/database is needed. The ZIP contains the approved retained global response bodies, source catalog, original audit code and input hash manifest; existing stock/index research datasets remain repository prerequisites. It is a compact review package, not a standalone copy of the entire research repository. The 108 MB independent field-comparison CSV is omitted and reproducible in the worktree.

No authenticated acquisition is authorized by either proposal manifest. The priority NSE proposal contains exactly seven requests. Provider/issuer evidence requests require separate authorization and source rights. Stop after TECH-FIX-01.
''')
    files=set()
    for p in OUT.rglob('*'):
        if not p.is_file() or p.suffix=='.zip' or p.name in ('SHA256SUMS','ZIP_RECEIPT.json','INDEPENDENT_COMPARISONS.csv','BROWSER_FAILURE.json','BROWSER_PARTIAL.json'):continue
        if 'charts-before' in p.parts:continue
        files.add(p)
    files.update(p for p in (ROOT/'experiments/technical_fix_v1').glob('*') if p.is_file())
    # Supply exact repaired files as well as their review diff.
    files.update(ROOT/r['path'] for r in changes)
    for row in read(N/'VALIDATED_PROVIDER_CANDLES.csv'):files.add(ROOT/row['source_file'])
    files.add(ROOT/'artifacts/technical-integration-v1/raw/SP500-297786f67b94948f56885dca236445c7a879d015acdf137dba387995db900157.json')
    files.update(p for p in (A/'code').glob('*') if p.is_file())
    files.add(ROOT/'artifacts/technical-integration-v2/inputs/global.json.gz')
    (OUT/'SHA256SUMS').write_text(''.join(sha(p)+'  '+str(p.relative_to(ROOT))+'\n' for p in sorted(files)))
    files.add(OUT/'SHA256SUMS')
    target=OUT/'TECH-FIX-01-review.zip'
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(files):
            entry=zipfile.ZipInfo(str(p.relative_to(ROOT)),date_time=(2026,11,5,0,0,0));entry.compress_type=zipfile.ZIP_DEFLATED;entry.external_attr=0o100644<<16
            z.writestr(entry,p.read_bytes())
    with zipfile.ZipFile(target) as z:
        assert z.testzip() is None
        for p in files:assert hashlib.sha256(z.read(str(p.relative_to(ROOT)))).hexdigest()==sha(p)
    save('ZIP_RECEIPT.json',dict(path=str(target.relative_to(ROOT)),sha256=sha(target),bytes=target.stat().st_size,files=len(files),crc_and_hashes_verified=True))
    print(json.dumps(json.loads((OUT/'ZIP_RECEIPT.json').read_text()),indent=2))
if __name__=='__main__':main()
