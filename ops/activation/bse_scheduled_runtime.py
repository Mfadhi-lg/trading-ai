"""One captured browser response through the verified bounded ingestion path."""
import hashlib
import json
from pathlib import Path
import signal
import sys

sys.path[:0] = ['/activation', '/app']
from nse_results_runtime import run, require


def main():
    from scrapper.sources.exchange.browser_results import RetainedBSEBrowserResults
    checks = json.loads(Path('/input/checks.json').read_text())
    for name, digest in checks['application_hashes'].items():
        require(hashlib.sha256((Path('/app') / name).read_bytes()).hexdigest() == digest,
                'Canonical application source changed: ' + name)
    for name, digest in checks['operator_hashes'].items():
        require(hashlib.sha256((Path('/activation') / name).read_bytes()).hexdigest() == digest,
                'Operator source changed: ' + name)
    source = RetainedBSEBrowserResults.load(receipt_path=Path('/input/receipt.json'),
        raw_path=Path('/input/response.raw'), verification_path=Path('/input/verification.json'))
    return run(source=source, verify_only='--verify-only' in sys.argv)


if __name__ == '__main__':
    def deadline(*_):
        raise RuntimeError('300-second deadline; preserve partial results; no retry')
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(300)
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({'stage': 'stop', 'error': str(exc), 'automatic_retry': False}), flush=True)
        sys.exit(2)
