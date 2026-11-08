"""Host-side lifecycle control; standard-library only, safe with stale PID files."""
import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ''):
    # ./scraper invokes this file directly through the host's stdlib Python.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scrapper.commands.browser_rating_host_config import (
    ROOT, LEGACY_FOLDER, PID_FILE, LOCK_FILE, LOG_FILE, PYTHON,
    assert_host_targets, bridge_command, environment_check_command,
)

FOLDER = LEGACY_FOLDER  # compatibility import; settings own new destinations
WORKER = ROOT / 'scrapper/commands/browser_rating_headed.py'


from types import SimpleNamespace
from ops.host import browser_rating_lifecycle as _owner


def _hooks():
    return SimpleNamespace(
        LEGACY_FOLDER=LEGACY_FOLDER,
        LOCK_FILE=LOCK_FILE,
        LOG_FILE=LOG_FILE,
        PID_FILE=PID_FILE,
        PYTHON=PYTHON,
        Path=Path,
        ROOT=ROOT,
        WORKER=WORKER,
        __doc__=__doc__,
        argparse=argparse,
        assert_host_targets=assert_host_targets,
        bridge_command=bridge_command,
        environment_check_command=environment_check_command,
        fcntl=fcntl,
        json=json,
        os=os,
        running_pid=running_pid,
        signal=signal,
        start=start,
        stop=stop,
        subprocess=subprocess,
        sys=sys,
        time=time,
    )


def running_pid():
    return _owner.running_pid(hooks=_hooks())


def start():
    return _owner.start(hooks=_hooks())


def stop():
    return _owner.stop(hooks=_hooks())


def main():
    return _owner.main(hooks=_hooks())


if __name__ == '__main__':
    raise SystemExit(main())
