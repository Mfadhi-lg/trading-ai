"""Run the user's visible Chromium flow; import PDFs through the live DB bridge."""
import argparse
import base64
import hashlib
import json
import subprocess
import sys
import signal
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urljoin
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from playwright.sync_api import sync_playwright, TimeoutError
from scrapper.config import assert_configured_writable_targets
from scrapper.commands.browser_rating_host_config import assert_host_targets, bridge_command
from scrapper.documents.recovery.browser_rating.protocol import exchange as bridge_exchange


from types import SimpleNamespace
from ops.host import browser_rating_runner as _owner


def _hooks():
    return SimpleNamespace(
        Path=Path,
        PublisherRateLimit=PublisherRateLimit,
        TimeoutError=TimeoutError,
        argparse=argparse,
        assert_configured_writable_targets=assert_configured_writable_targets,
        assert_host_targets=assert_host_targets,
        base64=base64,
        bridge_command=bridge_command,
        bridge_exchange=bridge_exchange,
        datetime=datetime,
        hashlib=hashlib,
        hide_mac_browser=hide_mac_browser,
        json=json,
        signal=signal,
        subprocess=subprocess,
        sync_playwright=sync_playwright,
        sys=sys,
        threading=threading,
        time=time,
        timezone=timezone,
        urljoin=urljoin,
    )


PublisherRateLimit = _owner.PublisherRateLimit


def hide_mac_browser(browser):
    return _owner.hide_mac_browser(browser, hooks=_hooks())


def main():
    return _owner.main(hooks=_hooks())


if __name__ == '__main__':
    main()
