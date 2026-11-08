import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from . import finish

class FinishTests(unittest.TestCase):
    def test_uncertain_delivery_never_restarts_acquisition(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);hcd=root/'hcd02';hcd.mkdir()
            (root/'full-universe-manifest.json').write_text(json.dumps(dict(requests=[dict(request_id='fixture')])))
            (hcd/'special-full-manifest.json').write_text(json.dumps(dict(requests=[])))
            store=Mock();store.root=root
            store.db.execute.return_value.fetchall.return_value=[dict(request_id='fixture',status='STOPPED',raw_path=None,attempts=1)]
            with patch.object(finish,'HCD',hcd),patch('experiments.historical_candle_archive_v1.access.HistoricalStore',return_value=store),patch('ops.host.historical_candle_lifecycle.wake') as wake:
                result=finish.recover_local_stop(dict(reason='OperationalError'))
                self.assertIn('UNCERTAIN_PROVIDER_DELIVERY',result['status']);wake.assert_not_called();store.close.assert_called_once()
    def test_completed_campaign_runs_chart_verification_browser_then_package(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);hcd=root/'hcd02';hcd.mkdir();out=root/'output';out.mkdir()
            (hcd/'full-campaign.json').write_text(json.dumps(dict(phase='FINISHED',status='ACQUISITION_PASS_FINISHED')))
            (hcd/'owner-repair-report-ready.json').write_text('{}')
            (hcd/'candle-runtime.json').write_text(json.dumps(dict(python='/fixture/python',dependencies='/fixture/deps')))
            (out/'CONTINUATION_RUNTIME.json').write_text(json.dumps(dict(node='/fixture/node')))
            with patch.object(finish,'HCD',hcd),patch.object(finish,'OUT',out),patch.object(finish,'snapshot'),patch.object(finish,'run_step') as step:
                finish.watch()
            self.assertEqual([c.args[0] for c in step.call_args_list],['FINAL_CHART_EXPORT','FINAL_SOURCE_VERIFICATION','FINAL_BROWSER_VALIDATION','FINAL_PORTABLE_PACKAGE'])
            result=json.loads((out/'COMPLETION_WORKER.json').read_text())
            self.assertEqual(result['state'],'FINISHED');self.assertEqual(result['provider_requests'],0)

    def test_authentication_or_storage_failure_never_automatically_restarts(self):
        for reason in ('STOP_PROVIDER_HTTP_401','STOP_100GIB_RESERVE','STOP_FINAL_RAW_CHECKSUM'):
            self.assertIn('NO_AUTOMATIC_RETRY',finish.recover_local_stop(dict(reason=reason))['status'])

if __name__=='__main__':unittest.main()
