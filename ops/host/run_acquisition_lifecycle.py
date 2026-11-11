"""Use the dispatcher's existing pinned Python environment, without shell eval."""
import os
from pathlib import Path
import plistlib
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'ops/activation'))
from acquisition_release import active_root
selected_root=active_root(ROOT)
prepared = selected_root / 'ops/activation/com.trading-ai.automatic-acquisition.plist'
config = plistlib.loads(prepared.read_bytes())
args = config['ProgramArguments']
if config.get('Label') != 'com.trading-ai.automatic-acquisition' or args[1:] != [
        str(selected_root / 'ops/activation/automatic_acquisition.py'), '--tick']:
    raise SystemExit('Lifecycle refused: prepared dispatcher identity differs')
python = Path(args[0])
if not python.is_file():
    raise SystemExit('Lifecycle refused: dispatcher Python environment is missing: ' + str(python))
env = os.environ.copy()
env['PYTHONDONTWRITEBYTECODE'] = '1'
env['PYTHONPATH'] = config['EnvironmentVariables']['PYTHONPATH']
if len(sys.argv)>1 and sys.argv[1]=='manual':
    if (selected_root/'ops/activation/postgresql-target.json').exists():
        entry='ops/host/postgresql_cli.py'
        os.execve(str(python),[str(python),'-B',str(selected_root/entry),*sys.argv[2:]],env)
    import subprocess
    raise SystemExit(subprocess.run(['docker','compose','--profile','manual','run','--rm','cli',*sys.argv[2:]],env=env).returncode)
entry = 'ops/host/acquisition_terminal.py' if len(sys.argv) > 1 and sys.argv[1] in ('status', 'coverage', 'bse', 'doctor', 'logs', 'monitor', 'shell', 'search', 'company') else 'ops/host/acquisition_lifecycle.py'
os.execve(str(python), [str(python), '-B', str(selected_root / entry), *sys.argv[1:]], env)
