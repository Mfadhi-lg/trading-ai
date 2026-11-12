"""Operator dispatch preserves literal arguments, host routing and exits."""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

class ManualCliContracts(unittest.TestCase):
    def test_declared_native_release_cannot_fall_back_when_target_is_missing(self):
        source=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);host=root/'ops/host';activation=root/'ops/activation'
            host.mkdir(parents=True);activation.mkdir(parents=True)
            launcher=host/'run_acquisition_lifecycle.py'
            launcher.write_bytes((source/'ops/host/run_acquisition_lifecycle.py').read_bytes())
            (activation/'acquisition_release.py').write_bytes((source/'ops/activation/acquisition_release.py').read_bytes())
            (activation/'automatic-acquisition-manifest.json').write_text(json.dumps({'files':{'ops/activation/postgresql-target.json':'expected'}}))
            result=subprocess.run([sys.executable,'-B',str(launcher),'manual','--help'],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('declared native target is missing',result.stderr)

    def invoke(self, args, exit_code, *, program='docker'):
        with tempfile.TemporaryDirectory() as directory:
            capture=Path(directory)/"args.json"
            executable=Path(directory)/program
            executable.write_text("#!"+sys.executable+"\nimport json,os,sys\nfrom pathlib import Path\nPath(os.environ['CAPTURE']).write_text(json.dumps(sys.argv[1:]))\nsys.exit(int(os.environ['EXIT_CODE']))\n")
            executable.chmod(0o700)
            env=dict(os.environ,PATH=directory+os.pathsep+os.environ['PATH'],CAPTURE=str(capture),EXIT_CODE=str(exit_code))
            result=subprocess.run([str(Path(__file__).resolve().parents[2]/'scraper'),*args],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,exit_code,result.stderr)
            return json.loads(capture.read_text())

    def test_literal_arguments_and_nonzero_exit(self):
        args=['exchange-refresh-universe','--dataset','nse_equity_master','--file','/inbox/file with spaces; $(touch forbidden).csv']
        self.assertEqual(self.invoke(args,23),['compose','--profile','manual','run','--rm','cli',*args])

    def test_no_arguments_opens_current_host_shell(self):
        self.assertEqual(self.invoke([],7,program='python3'),['-B','ops/host/run_acquisition_lifecycle.py','shell'])

    def test_root_help_uses_host_operator_cli(self):
        self.assertEqual(self.invoke(['--help'],0,program='python3'),['-B','ops/host/run_acquisition_lifecycle.py','shell','--help'])

    def test_monitor_uses_current_host_and_preserves_flags(self):
        self.assertEqual(self.invoke(['monitor','--once'],7,program='python3'),['-B','ops/host/run_acquisition_lifecycle.py','monitor','--once'])

    def test_removed_rating_shortcuts_do_not_start_legacy_workers(self):
        wrapper=Path(__file__).resolve().parents[2]/'scraper'
        for command in ('rating','ratings','downloads'):
            with self.subTest(command=command):
                result=subprocess.run([str(wrapper),command,'--once'],capture_output=True,text=True)
                self.assertEqual(result.returncode,2)
                self.assertIn('removed',result.stderr)
                self.assertIn('./scraper monitor',result.stderr)
