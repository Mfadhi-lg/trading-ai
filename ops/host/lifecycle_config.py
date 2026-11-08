"""Read-only wrapper settings; no lifecycle action occurs in this helper."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scrapper.commands.browser_rating_host_config import CONTAINER, assert_host_targets
from scrapper.config import assert_configured_writable_targets

# Keep the line-oriented legacy state file and host marker readable by old code.
STATE_VALUE = os.environ.get('SCRAPER_SHUTDOWN_STATE_PATH',
                             str(ROOT / '.scraper_shutdown_state'))
STATE_FILE = Path(STATE_VALUE)


def settings(action):
    if not STATE_VALUE.strip() or not CONTAINER.strip():
        raise RuntimeError('Lifecycle state path and bridge container must be nonempty')
    for value in (str(STATE_FILE), CONTAINER):
        if any(char in value for char in ('\n', '\r', '\t')):
            raise RuntimeError('Lifecycle settings cannot contain line separators')
    if action in ('shutdown', 'wake'):
        assert_configured_writable_targets(STATE_FILE)
        assert_host_targets()
    return str(STATE_FILE), CONTAINER


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('shutdown', 'wake', 'status'))
    args = parser.parse_args()
    try:
        print('\n'.join(settings(args.action)))
    except RuntimeError as exc:
        print(f'Lifecycle configuration refused: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
