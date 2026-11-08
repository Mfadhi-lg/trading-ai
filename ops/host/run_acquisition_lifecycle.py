"""Use the dispatcher's existing pinned Python environment, without shell eval."""
import os
from pathlib import Path
import plistlib
import sys

ROOT = Path(__file__).resolve().parents[2]
prepared = ROOT / 'ops/activation/com.trading-ai.automatic-acquisition.plist'
config = plistlib.loads(prepared.read_bytes())
args = config['ProgramArguments']
if config.get('Label') != 'com.trading-ai.automatic-acquisition' or args[1:] != [
        str(ROOT / 'ops/activation/automatic_acquisition.py'), '--tick']:
    raise SystemExit('Lifecycle refused: prepared dispatcher identity differs')
python = Path(args[0])
if not python.is_file():
    raise SystemExit('Lifecycle refused: dispatcher Python environment is missing: ' + str(python))
env = os.environ.copy()
env['PYTHONDONTWRITEBYTECODE'] = '1'
env['PYTHONPATH'] = config['EnvironmentVariables']['PYTHONPATH']
entry = 'ops/host/acquisition_terminal.py' if len(sys.argv) > 1 and sys.argv[1] in ('status', 'coverage', 'bse', 'doctor', 'logs', 'monitor', 'shell', 'search', 'company') else 'ops/host/acquisition_lifecycle.py'
os.execve(str(python), [str(python), '-B', str(ROOT / entry), *sys.argv[1:]], env)
