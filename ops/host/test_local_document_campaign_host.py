import json
from pathlib import Path
import tempfile
import unittest
from local_document_campaign import remaining_contents


class HostCampaignTests(unittest.TestCase):
    def test_completed_selection_does_not_need_more_reservations(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'receipts').mkdir()
            manifest={'contents':[{'content_id':1},{'content_id':2}]}
            self.assertEqual(remaining_contents(manifest,root,'frozen'),[1,2])
            for cid,status in ((1,'ok'),(2,'failed')):
                (root/'receipts'/('content-%s.json'%cid)).write_text(json.dumps({
                    'campaign_sha256':'frozen','content_id':cid,'extraction_id':cid,'extraction_status':status}))
            self.assertEqual(remaining_contents(manifest,root,'frozen'),[])
            with self.assertRaises(RuntimeError):remaining_contents(manifest,root,'different')
