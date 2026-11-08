"""Review-only fresh read-only canary; no snapshot or activation."""
import importlib.util
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[5]
spec = importlib.util.spec_from_file_location("readonly_host", ROOT / "ops/docker/canary/ar013_readonly_host.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
host.OUT = Path(__file__).resolve().parent / "fresh-canary"
sys.exit(host.main())
