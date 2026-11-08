"""Inspect exact candidate bytes; emit locations/rules, never matching contents."""
from pathlib import Path
import ast
import hashlib
import importlib.metadata
import importlib.util
import json
import re
import subprocess
import sys

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
EXPORT=ROOT/'github-source-candidate-20261023'

def dump(name,data):
    (OUT/name).write_text(json.dumps(data,indent=2)+'\n')

def main():
    # Known local credential values remain in process memory only.
    known=[]
    for name in ('.screener_csrf','.screener_session','.env'):
        p=ROOT/name
        if not p.is_file():continue
        raw=p.read_bytes().strip()
        if name!='.env':
            if len(raw)>=16:known.append(raw)
        for line in raw.splitlines():
            if b'=' in line and not line.lstrip().startswith(b'#'):
                key,value=line.split(b'=',1)
                value=value.strip().strip(b'\"\'')
                if len(value)>=16 and re.search(rb'(?i)key|token|secret|password|session|csrf',key):
                    known.append(value)
    patterns={
        'private_key':rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
        'github_token':rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})',
        'aws_access_id':rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
        'provider_key':rb'\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{30,}',
        'credential_assignment':rb'''(?i)\b(?:api_key|access_token|auth_token|sessionid|csrftoken|password)\s*[=:]\s*["']([A-Za-z0-9_+/.=-]{20,})["']''',
        'basic_auth_url':rb'https?://[^\s/:]{2,}:[^\s/@]{4,}@',
    }
    paths=sorted(p for p in EXPORT.rglob('*') if p.is_file() and '.git' not in p.relative_to(EXPORT).parts)
    secrets=[];machine=[];invalid=[];hidden=[];binary=[];large=[];inventory=[];imports=[];missing=[];data_refs=[];local_scripts=[];conditional=[]
    for p in paths:
        name=str(p.relative_to(EXPORT));data=p.read_bytes()
        inventory.append(dict(path=name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
        if p.is_symlink():invalid.append({'path':name,'rule':'symlink'})
        if b'\0' in data or data.startswith((b'SQLite format 3',b'PK\x03\x04',b'%PDF',b'\x1f\x8b')):binary.append(name)
        if len(data)>1024*1024:large.append({'path':name,'bytes':len(data)})
        if any(part.startswith('.') for part in p.relative_to(EXPORT).parts):hidden.append(name)
        for value in known:
            if value in data:secrets.append({'path':name,'rule':'known_local_credential_exact_match'})
        for rule,rx in patterns.items():
            for m in re.finditer(rx,data):
                secrets.append({'path':name,'line':data[:m.start()].count(b'\n')+1,'rule':rule})
        for i,line in enumerate(data.splitlines(),1):
            if re.search(rb'/Users/|/Volumes/|/private/(?:tmp|var)/',line):machine.append({'path':name,'line':i})
        if p.suffix!='.py':continue
        try:
            tree=ast.parse(data,filename=name)
            compile(tree,name,'exec')
        except Exception as exc:
            invalid.append({'path':name,'rule':type(exc).__name__});continue
        package='.'.join(p.relative_to(EXPORT).parts[:-1])
        for node in ast.walk(tree):
            mods=[]
            if isinstance(node,ast.Import):mods=[a.name for a in node.names]
            elif isinstance(node,ast.ImportFrom):
                if node.level:
                    try:mods=[importlib.util.resolve_name('.'*node.level+(node.module or ''),package)]
                    except (ImportError,ValueError):missing.append({'path':name,'line':node.lineno,'module':'invalid relative import'})
                elif node.module:mods=[node.module]
            for mod in mods:
                local=EXPORT.joinpath(*mod.split('.'))
                if mod.split('.')[0] in {'scrapper','ops','scripts'}:
                    if not local.is_dir() and not local.with_suffix('.py').is_file():missing.append({'path':name,'line':node.lineno,'module':mod})
                elif mod.split('.')[0] not in sys.stdlib_module_names:
                    if mod=='acquisition_financial_renderer':
                        conditional.append({'path':name,'line':node.lineno,'module':mod,'disposition':'deployment-only optional renderer; ModuleNotFoundError branch falls back to included scrapper.view.render outside /activation'})
                        continue
                    if not (p.parent/(mod.split('.')[0]+'.py')).exists():
                        candidates=list((EXPORT/'ops/activation').glob(mod.split('.')[0]+'.py'))
                        if candidates:
                            local_scripts.append({'path':name,'line':node.lineno,'module':mod,'source':str(candidates[0].relative_to(EXPORT)),'note':'local script module; callers supply sys.path, not a third-party package'})
                        else:imports.append({'path':name,'line':node.lineno,'module':mod.split('.')[0]})
            if isinstance(node,ast.Constant) and isinstance(node.value,str):
                value=node.value
                if len(value)<260 and '\n' not in value and value.startswith(('artifacts/','docs/cleanup/','ops/docker/release/','OLD/','resources/')):
                    if not (EXPORT/value).exists():data_refs.append({'path':name,'line':node.lineno,'reference':value})
    # Correlate imports with declared lock distributions without importing source.
    packages=importlib.metadata.packages_distributions()
    aliases={'PIL':'pillow','bs4':'beautifulsoup4','yaml':'pyyaml','pptx':'python-pptx','cv2':'opencv-python','fitz':'pymupdf','prompt_toolkit':'prompt-toolkit'}
    locks='\n'.join(p.read_text() for p in EXPORT.rglob('requirements*.lock'))
    locks+='\n'+(EXPORT/'ops/host/browser-requirements.lock').read_text()
    declared=set(re.findall(r'^([A-Za-z0-9_.-]+)==',locks,re.M))
    normalize=lambda s:s.lower().replace('_','-')
    declared={normalize(s) for s in declared}
    dependency=[]
    for module in sorted({r['module'] for r in imports}):
        distributions=packages.get(module,[aliases.get(module,module)])
        dependency.append({'import':module,'distributions':distributions,'declared_in_lock':any(normalize(d) in declared for d in distributions),'occurrences':sum(r['module']==module for r in imports)})
    dump('candidate-files.json',inventory)
    dump('secret-scan.json',{'files_scanned':len(paths),'known_local_credential_matches':sum(r['rule']=='known_local_credential_exact_match' for r in secrets),'findings':secrets,'scope':'Every candidate file, no size skips; pattern and known-local-value comparison; no values/digests logged','limitations':'Heuristics do not prove absence of arbitrary/encoded unknown secrets.'})
    dump('static-integrity.json',{'python_compilation_issues':invalid,'missing_local_import_modules':missing,'local_script_imports':local_scripts,'third_party_dependencies':dependency,'third_party_import_locations':imports,'machine_path_locations':machine,'missing_literal_data_references':data_refs,'binary_or_database_magic':binary,'over_1MiB_files':large,'hidden_files':hidden,'largest_file':max(inventory,key=lambda x:x['bytes']),'total_bytes':sum(r['bytes'] for r in inventory),'limits':'AST module resolution, not execution of every command; dynamic imports and constructed paths require runtime-specific review.'})
    baseline=json.loads((OUT/'original-baseline.json').read_text())
    mapping=json.loads((OUT/'original-to-export.json').read_text())
    drift=[r['source'] for r in mapping if r['operation'] in ('copy','export_only_modification') and hashlib.sha256((ROOT/r['source']).read_bytes()).hexdigest()!=r['sha256']]
    dump('conditional-deployment-imports.json',conditional)
    dump('preservation.json',{'head_unchanged':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip()==baseline['head'],'original_index_unchanged':hashlib.sha256((ROOT/'.git/index').read_bytes()).hexdigest()==baseline['index_sha256'],'selected_original_source_drift':drift,'scope':'No original tracked or production path intentionally written. Head/index and selected source hashes compared; live databases not opened, hashed or frozen.'})
    print(json.dumps({'files':len(paths),'bytes':sum(r['bytes'] for r in inventory),'secret_candidates':len(secrets),'syntax_errors':len(invalid),'missing_local_modules':len(missing),'machine_path_occurrences':len(machine),'undeclared_imports':[r['import'] for r in dependency if not r['declared_in_lock']],'source_drift':drift},indent=2))

if __name__=='__main__':main()
