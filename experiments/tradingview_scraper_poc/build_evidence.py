"""Trusted static analysis and packaging. Never imports third-party source."""
import ast
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parents[1]
OUT = WORKSPACE / 'artifacts/tradingview-scraper-poc/tv01'
VENDOR = next((ROOT / 'vendor').glob('tradingview-scraper-*'))
COMMIT = '68ea546a7a4b78b49da2b7417824b8ee44d237d5'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name, data):
    (OUT / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def static_audit():
    with tarfile.open(ROOT / 'upstream.tar.gz') as archive:
        source_files = [member for member in archive.getmembers() if member.isfile()]
        for member in source_files:
            relative = Path(member.name).relative_to(VENDOR.name)
            actual = VENDOR / relative
            if not actual.is_file() or actual.read_bytes() != archive.extractfile(member).read():
                raise SystemExit('Upstream source differs from pinned archive: ' + str(relative))
    actual_files = [p for p in VENDOR.rglob('*') if p.is_file()]
    if len(actual_files) != len(source_files):
        raise SystemExit('Unexpected extra upstream files')
    write('upstream-integrity.json', dict(files_checked=len(source_files), differences=[],
          method='Byte comparison of extracted source against pinned GitHub archive'))
    calls = []
    urls = set()
    manifests = {}
    for path in sorted(VENDOR.rglob('*')):
        if not path.is_file():
            continue
        relative = str(path.relative_to(VENDOR))
        manifests[relative] = sha(path)
        if path.suffix != '.py':
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                urls.update(re.findall(r'(?:https?|wss?)://[^\s\"\'<>]+', node.value))

        class Visitor(ast.NodeVisitor):
            def __init__(self):
                self.scope = []

            def visit_ClassDef(self, node):
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

            def visit_FunctionDef(self, node):
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

            def visit_Call(self, node):
                function = ast.unparse(node.func)
                if function in ('requests.get', 'requests.post', 'create_connection') or function.endswith(('.ws.send', '.ws.recv', '.ws.close')):
                    calls.append(dict(file=relative, function='.'.join(self.scope), line=node.lineno,
                                      call=function, execution='PREVENTED_REAL_TRANSPORT'))
                self.generic_visit(node)

        Visitor().visit(tree)
    write('upstream-source-sha256.json', manifests)
    write('static-network-inventory.json', dict(calls=calls, url_literals=sorted(urls),
          caveat='AST direct-call inventory plus reviewed wrapper call paths; documentation/UA URL strings are not necessarily destinations.'))
    head = json.loads((OUT / 'github-head.json').read_text())
    write('repository-pin.json', dict(repository='https://github.com/mnwato/tradingview-scraper',
          commit=COMMIT, commit_date=head['commit']['committer']['date'],
          package_version='0.4.22', license='MIT; Copyright (c) 2021 mnajmi',
          archive_url=f'https://codeload.github.com/mnwato/tradingview-scraper/tar.gz/{COMMIT}',
          archive_sha256=sha(ROOT / 'upstream.tar.gz'), license_sha256=sha(VENDOR / 'LICENSE'),
          verification='HTTPS GitHub archive pinned by commit; file SHA256 inventory. No signature validation claimed.'))
    lines = ['# Exact direct network functions (static inventory)', '', 'All real transports prevented; only explicitly mocked paths were exercised.', '']
    for c in calls:
        lines.append(f"- `{c['file']}:{c['line']}` — `{c['function']}` → `{c['call']}`")
    (OUT / 'network-functions.md').write_text('\n'.join(lines) + '\n')


def csv_inventory():
    files = subprocess.check_output(['rg', '--files', '-uuu', '-g', '*.csv', '-g', '!**/.git/**'], cwd=WORKSPACE, text=True).splitlines()
    candidates = []
    count = 0
    for name in files:
        if name.startswith(('experiments/tradingview_scraper_poc/', 'artifacts/tradingview-scraper-poc/')):
            continue
        path = WORKSPACE / name
        if path.is_symlink():
            continue
        count += 1
        try:
            with path.open(encoding='utf-8-sig', newline='') as f:
                header = next(csv.reader(f), [])
        except (UnicodeError, OSError, csv.Error):
            continue
        lower = {x.lower() for x in header}
        if {'open', 'high', 'low', 'close'} <= lower:
            candidates.append(dict(path=name, sha256=sha(path), columns=header,
                                   provenance='Existing generated research CSV; not identified as TradingView export'))
    write('existing-csv-inventory.json', dict(csv_headers_inspected=count, ohlc_candidates=candidates,
          tradingview_export_identified=False, limitation='Header inspection and research provenance review; no filename/header can conclusively prove export origin.'))


def preservation():
    before = json.loads((OUT / 'preservation-before.json').read_text())
    mismatches = [name for name, digest in before.items() if not (WORKSPACE / name).is_file() or sha(WORKSPACE / name) != digest]
    write('preservation-verification.json', dict(files_checked=len(before), mismatches=mismatches,
          scope='Existing earnings-reaction research source and TA evidence; excludes .DS_Store and __pycache__. No calculations rerun.',
          task_write_roots=['experiments/tradingview_scraper_poc/', 'artifacts/tradingview-scraper-poc/tv01/']))
    if mismatches:
        raise SystemExit('Preservation mismatch; packaging stopped')


def package():
    # Allowlist only public source, task code, synthetic data and reports.
    for folder in ('source', 'upstream'):
        (OUT / folder).mkdir(exist_ok=True)
    for path in sorted(ROOT.rglob('*')):
        relative = path.relative_to(ROOT)
        if not path.is_file() or any(part in ('.venv', '__pycache__', 'vendor') for part in relative.parts):
            continue
        if path.suffix not in ('.py', '.sb', '.json', '.csv', '.md'):
            continue
        destination = OUT / 'source' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    for path in sorted(VENDOR.rglob('*')):
        if path.is_file():
            destination = OUT / 'upstream' / path.relative_to(VENDOR)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
    excluded = {'SHA256SUMS', 'tv01-review.zip', 'review-zip.sha256', 'github-head.json'}
    members = [p for p in sorted(OUT.rglob('*')) if p.is_file() and p.name not in excluded]
    manifest = ''.join(f'{sha(p)}  {p.relative_to(OUT)}\n' for p in members)
    (OUT / 'SHA256SUMS').write_text(manifest)
    members.append(OUT / 'SHA256SUMS')
    with zipfile.ZipFile(OUT / 'tv01-review.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in members:
            info = zipfile.ZipInfo(str(Path('tv01') / path.relative_to(OUT)), (2026, 11, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    (OUT / 'review-zip.sha256').write_text(sha(OUT / 'tv01-review.zip') + '  tv01-review.zip\n')
    print(json.dumps(dict(files_in_zip=len(members), zip_bytes=(OUT / 'tv01-review.zip').stat().st_size)))


if __name__ == '__main__':
    static_audit()
    csv_inventory()
    preservation()
    package()
