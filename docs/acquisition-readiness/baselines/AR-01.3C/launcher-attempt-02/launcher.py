"""Offline-only explicit Docker argv builder. This module cannot execute Docker."""
import ast
import hashlib
import json
from pathlib import Path

IMAGE = 'trading-ai-c281-release@sha256:4d289faa67334bc870d4f2a6a181feecee777fd427eac209b9adef68ecab0238'
CODE_SHA256 = '937352ea7d4a5fae5dbcaf879d9821f4b5ae3ebd5bb8d5c6acd7d238654ed992'
ROOT = Path(__file__).resolve().parents[5]
CODE_PATH = ROOT / 'docs/acquisition-readiness/baselines/AR-01.3C/execution/reviewed-inline-code.py'


def validate_argv(argv, code):
    if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
        raise ValueError('Arguments must be a list of strings')
    if argv[:2] != ['docker', 'run']:
        raise ValueError('Wrong Docker subcommand')
    # The explicitly constructed reviewed prefix has 39 elements. Its only
    # positional command suffix is IMAGE, -c, the intact Python code argument.
    if len(argv) != 42 or argv[39:] != [IMAGE, '-c', code]:
        raise ValueError('Image/command positional boundary differs')
    if argv.count(IMAGE) != 1:
        raise ValueError('Image must occur exactly once')
    if any(not x or x.isspace() or '\n' in x or '\r' in x for x in argv[:40]):
        raise ValueError('Blank or newline argument before/at image')
    if hashlib.sha256(code.encode('utf-8')).hexdigest() != CODE_SHA256:
        raise ValueError('Reviewed application code bytes changed')
    if argv[40] != '-c' or argv[41] != code:
        raise ValueError('Python code split or changed')
    ast.parse(code)  # Compile structure only; never evaluate application code.


def build_docker_argv():
    code = CODE_PATH.read_bytes().decode('utf-8')
    if hashlib.sha256(CODE_PATH.read_bytes()).hexdigest() != CODE_SHA256:
        raise ValueError('Original reviewed application script hash differs')
    argv = [
        'docker', 'run',
        '--rm', '--pull', 'never', '--no-healthcheck', '--network', 'none',
        '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--cpus', '0.5', '--memory', '1536m', '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m',
        '--mount', 'type=volume,src=trading-ai_scraper-data,dst=/data',
        '--mount', 'type=bind,src=/Users/mohamedfadhi/Fadhi/trading-ai/docs/acquisition-readiness/baselines/AR-01.3C/approved-config/runtime-identity-core.json,dst=/run/trading-ai/runtime-identity.json,readonly',
        '--mount', 'type=bind,src=/Users/mohamedfadhi/Fadhi/trading-ai/docs/acquisition-readiness/baselines/AR-01.3C/approved-config/store-sentinel.json,dst=/data/.trading_ai_runtime_identity.json,readonly',
        '--mount', 'type=bind,src=/Users/mohamedfadhi/Fadhi/trading-ai/docs/acquisition-readiness/baselines/AR-01.3C/approved-config/core-documents-sentinel.json,dst=/data/snapshots/.trading_ai_runtime_identity.json,readonly',
        '--env', 'TRADING_AI_ENV=production',
        '--env', 'TRADING_AI_RUNTIME_IDENTITY_FILE=/run/trading-ai/runtime-identity.json',
        '--env', 'SCRAPER_DB_PATH=/data/scraper.db',
        '--env', 'SCRAPER_STORE_PATH=/data',
        '--env', 'SCRAPER_DOCUMENT_STORE_PATH=/data/snapshots',
        '--entrypoint', 'python',
        IMAGE, '-c', code,
    ]
    validate_argv(argv, code)
    return argv


if __name__ == '__main__':
    # Emitting JSON is the only CLI action. No shell, Docker, DB or canary call.
    print(json.dumps(build_docker_argv(), indent=2))
