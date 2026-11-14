#!/usr/bin/env python3
"""Static, tracked-source SQLite review gate. Never opens a database."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / 'docs/database/sqlite-usage.json'
CLASSES = {'live application', 'secondary operational store', 'historical snapshot',
           'compatibility/test', 'transfer/admin', 'ephemeral scratch'}
DIALECT = re.compile(
    r'\b(?:PRAGMA\s+\w+|sqlite_(?:master|schema)|json_(?:extract|each|tree|valid|type|array_length)\s*\('
    r'|INSERT\s+OR\s+(?:IGNORE|REPLACE)|REPLACE\s+INTO|last_insert_rowid\s*\('
    r'|(?:strftime|julianday|datetime|date|time|unixepoch|timediff)\s*\('
    r'|json_(?:object|array|group_array|group_object|quote|remove|replace|insert|set|patch)\s*\('
    r'|GROUP_CONCAT\s*\(|AUTOINCREMENT\b)', re.I)
CONFIG = re.compile(r'sqlite3?(?:\.connect|\s)|\.sqlite3?\b|scraper\.db\b|SCRAPER_DB_PATH|TRADING_AI_DB_BACKEND', re.I)
ROOTS = {'scrapper', 'ops', 'scripts', 'experiments'}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def syntax(node):
    """Stable AST evidence across Python versions (ast.dump defaults differ)."""
    if isinstance(node, ast.AST):
        return {'node': type(node).__name__, **{
            key: syntax(value) for key, value in ast.iter_fields(node)
            if value is not None and value != []}}
    if isinstance(node, list):
        return [syntax(item) for item in node]
    if isinstance(node, (bytes, complex)) or node is Ellipsis:
        return {'literal': repr(node)}
    return node


def syntax_digest(node):
    return digest(json.dumps(syntax(node), sort_keys=True, separators=(',', ':')))


def tracked_paths(root):
    return sorted(subprocess.check_output(
        ['git', 'ls-files', '-z', '--cached'], cwd=root).decode().split('\0')[:-1])


def in_scope(path):
    p = Path(path)
    if any(part in {'tests', 'migrations', 'postgresql_migrations', 'vendor', '__pycache__'} for part in p.parts):
        return False
    if p.name.startswith('test_') or p.name.endswith('_test.py'):
        return False
    return p.parts[0] in ROOTS or (len(p.parts) == 1 and not p.name.startswith('.')) or path.startswith('.github/workflows/')


def python_sites(source):
    """Conservative syntax inventory, not control-flow or SQL execution proof."""
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    aliases = {'sqlite3.connect', 'sqlite3.dbapi2.connect'}
    # Include local imports and simple assignments; over-approximate shadowing.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.name in {'sqlite3', 'sqlite3.dbapi2'}:
                    aliases.add((item.asname or item.name) + '.connect')
        if isinstance(node, ast.ImportFrom) and node.module in {'sqlite3', 'sqlite3.dbapi2'}:
            for item in node.names:
                if item.name == 'connect':
                    aliases.add(item.asname or item.name)
                elif item.name == 'dbapi2':
                    aliases.add((item.asname or item.name) + '.connect')
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                if ast.unparse(node.value) in aliases:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        name = ast.unparse(target)
                        if name not in aliases:
                            aliases.add(name)
                            changed = True

    sites = []
    for node in ast.walk(tree):
        kind = None
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name in aliases:
                kind = 'direct connection'
            elif isinstance(node.func, ast.Attribute) and node.func.attr in {'execute', 'executemany', 'executescript'}:
                if node.args and not isinstance(node.args[0], ast.Constant):
                    kind = 'dynamic query'
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and DIALECT.search(node.value):
            kind = 'SQLite SQL'
        if kind is None:
            continue
        scope = node
        symbols, branches = [], []
        cursor = node
        while cursor in parents:
            parent = parents[cursor]
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.append(parent.name)
                if scope is node:
                    scope = parent
            if isinstance(parent, (ast.If, ast.IfExp)):
                # Only a hint for human review; presence of a condition is NOT
                # automatically accepted as a backend safety guard.
                test = ast.unparse(parent.test)
                if re.search(r'postgres|backend|enabled', test, re.I):
                    arm = 'body' if cursor in (parent.body if isinstance(parent.body, list) else [parent.body]) else 'else'
                    branches.append({'condition_sha256': syntax_digest(parent.test), 'arm': arm, 'line': parent.lineno})
            cursor = parent
        sites.append({'line': node.lineno, 'symbol': '.'.join(reversed(symbols)) or '<module>',
                      'kind': kind, 'sha256': syntax_digest(node),
                      'scope_sha256': syntax_digest(scope),
                      'branch_hints': sorted(branches, key=lambda b: b['line'])})
    return sorted(sites, key=lambda s: (s['line'], s['kind'], s['sha256']))


def scan(root, paths=None, *, index=False):
    paths = tracked_paths(root) if paths is None else sorted(paths)
    sources, entries = {}, []
    for path in paths:
        if not in_scope(path):
            continue
        p = root / path
        if not p.is_file() or p.is_symlink():
            continue
        # Read only source/config extensions and extensionless launchers.
        if p.suffix not in {'', '.py', '.sh', '.yml', '.yaml', '.sql'} and not p.name.startswith('Dockerfile'):
            continue
        source = (subprocess.check_output(['git', 'show', ':' + path], cwd=root).decode('utf-8')
                  if index else p.read_text(encoding='utf-8'))
        sources[path] = source
        python = p.suffix == '.py' or bool(re.match(r'#![^\n]*python', source))
        if python:
            sites = python_sites(source)  # Syntax errors fail closed.
        else:
            sites = [{'line': n, 'symbol': '<embedded/config>', 'kind': 'embedded/config',
                      'sha256': digest(line), 'scope_sha256': digest(source), 'branch_hints': []}
                     for n, line in enumerate(source.splitlines(), 1)
                     if CONFIG.search(line) or DIALECT.search(line)]
        if sites:
            entries.append({'path': path, 'source_sha256': digest(source), 'sites': sites})
    # Candidate callers include imports, shell execs, Docker COPY and retained
    # script references. These are source references, never claims of live use.
    for entry in entries:
        path = entry['path']
        module = path.removesuffix('.py').replace('/', '.')
        basename = Path(path).stem
        needles = {path, module, Path(path).name} if '/' in path else {path}
        import_reference = re.compile(r'\b(?:from|import)\s+.*\b' + re.escape(basename) + r'\b')
        entry['caller_references'] = [
            {'path': other, 'line': n}
            for other, source in sorted(sources.items()) if other != path
            for n, line in enumerate(source.splitlines(), 1)
            if any(needle in line for needle in needles) or import_reference.search(line)]
    return entries


def check(entries, manifest):
    errors = []
    reviewed = manifest.get('files', [])
    if len({e['path'] for e in reviewed}) != len(reviewed):
        errors.append('Duplicate inventory paths')
    expected = {entry['path']: entry for entry in reviewed}
    actual = {entry['path']: entry for entry in entries}
    for path in sorted(expected.keys() | actual.keys()):
        old, new = expected.get(path), actual.get(path)
        if old is None:
            errors.append(f'{path}: unclassified source sites')
            continue
        if new is None:
            errors.append(f'{path}: stale inventory; review removal')
            continue
        for field in ('store', 'intent', 'backend_guard', 'owner', 'disposition', 'follow_up'):
            if not isinstance(old.get(field), str) or not old[field].strip() or old[field] == 'UNCLASSIFIED':
                errors.append(f'{path}: missing reviewed {field}')
        if old.get('classification') not in CLASSES:
            errors.append(f'{path}: missing reviewed classification')
        if new.get('source_sha256') and old.get('reviewed_source_sha256') != new['source_sha256']:
            errors.append(f'{path}: source review acknowledgment is stale')
        for field in ('source_sha256', 'sites', 'caller_references'):
            if old.get(field) != new.get(field):
                errors.append(f'{path}: changed {field}; review SQLite/native routing and refresh evidence')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, help='Write candidate JSON; never updates review decisions')
    parser.add_argument('--index', action='store_true', help='Review staged sources, preserving unrelated working-tree edits')
    args = parser.parse_args()
    entries = scan(ROOT, index=args.index)
    if args.candidate:
        previous = {e['path']: e for e in json.loads(MANIFEST.read_text()).get('files', [])} if MANIFEST.exists() else {}
        for entry in entries:
            old = previous.get(entry['path'], {})
            for field in ('classification', 'store', 'intent', 'backend_guard', 'owner', 'disposition', 'follow_up', 'reviewed_source_sha256'):
                entry[field] = old.get(field, 'UNCLASSIFIED')
        args.candidate.write_text(json.dumps({'version': 1, 'files': entries}, indent=2) + '\n')
        print(f'Candidate only: {len(entries)} files; review every changed site before acceptance')
        return 0
    errors = check(entries, json.loads(MANIFEST.read_text()))
    for error in errors:
        print(error)
    print(f'{"FAIL" if errors else "PASS"}: {len(entries)} classified files, {sum(len(e["sites"]) for e in entries)} source sites')
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
