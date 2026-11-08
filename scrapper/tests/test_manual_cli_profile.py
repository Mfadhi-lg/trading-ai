"""Operator dispatch preserves literal arguments, shell behavior and exits."""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

class ManualCliContracts(unittest.TestCase):
    def invoke(self, args, exit_code):
        with tempfile.TemporaryDirectory() as directory:
            capture=Path(directory)/"args.json"
            docker=Path(directory)/"docker"
            docker.write_text("#!"+sys.executable+"\nimport json,os,sys\nfrom pathlib import Path\nPath(os.environ['CAPTURE']).write_text(json.dumps(sys.argv[1:]))\nsys.exit(int(os.environ['EXIT_CODE']))\n")
            docker.chmod(0o700)
            env=dict(os.environ,PATH=directory+os.pathsep+os.environ['PATH'],CAPTURE=str(capture),EXIT_CODE=str(exit_code))
            result=subprocess.run([str(Path(__file__).resolve().parents[2]/'scraper'),*args],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,exit_code)
            return json.loads(capture.read_text())

    def test_literal_arguments_and_nonzero_exit(self):
        args=['exchange-refresh-universe','--dataset','nse_equity_master','--file','/inbox/file with spaces; $(touch forbidden).csv']
        self.assertEqual(self.invoke(args,23),['compose','--profile','manual','run','--rm','cli',*args])

    def test_no_arguments_opens_same_shell(self):
        self.assertEqual(self.invoke([],0),['compose','--profile','manual','run','--rm','cli','shell'])

    def test_rating_monitor_python_entrypoint_and_live_flag(self):
        self.assertEqual(self.invoke(['ratings','--once'],7),['compose','--profile','manual','run','--rm','--entrypoint','python','cli','-m','scrapper.view.browser_rating_monitor','--once','--live'])
