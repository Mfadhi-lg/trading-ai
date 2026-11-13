"""Synthetic retained-receipt tests; no Passport or network access."""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scrapper.indices.benchmark_intraday import INVENTORY, KEY, load_benchmarks


class BenchmarkIntradayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        s = datetime.fromisoformat('2026-10-30T09:15:00+05:30')
        self.bars = [[(s+timedelta(minutes=5*i)).isoformat(), 100, 102, 99, 101, 0, 0] for i in range(75)]

    def load(self, bars=None, cutoff='2026-10-30T16:00:00+05:30'):
        raw = json.dumps({'data': {'candles': self.bars if bars is None else bars}}).encode()
        (self.root/'raw.json').write_bytes(raw)
        inv = self.root/INVENTORY
        inv.parent.mkdir(parents=True, exist_ok=True)
        inv.write_text(json.dumps({'datasets':[dict(instrument_key=KEY, unit='minutes', interval=5,
            source=dict(original_path='raw.json',sha256=hashlib.sha256(raw).hexdigest()))]}))
        return load_benchmarks('2026-10-30','2026-10-30',cutoff,self.root)

    def test_session_alignment(self):
        result = self.load()
        self.assertEqual(len(result['1-hour']), 7)
        self.assertEqual([r['start'][11:16] for r in result['4-hour']], ['09:15','13:15'])
        self.assertEqual(result['4-hour'][-1]['end'][11:16], '15:30')
        self.assertEqual(result['4-hour'][0]['close'], 101)
        self.assertEqual(len(result['4-hour'][0]['source_response_sha256s']), 1)

    def test_missing_invalid_conflict(self):
        invalid = [list(b) for b in self.bars]
        invalid[0][2] = 98
        for bars in (self.bars[1:], invalid):
            self.assertEqual(len(self.load(bars)['4-hour']), 1)
        conflict = list(self.bars[0]); conflict[4] = 100
        self.assertEqual(len(self.load(self.bars+[conflict])['4-hour']), 1)
        self.assertEqual(len(self.load(self.bars+[self.bars[0]])['4-hour']), 2)

    def test_no_unfinished_future(self):
        result = self.load(cutoff='2026-10-30T13:14:59+05:30')
        self.assertEqual(result['4-hour'], [])
        self.assertEqual(len(result['1-hour']), 3)
        result = self.load(cutoff='2026-10-30T13:15:00+05:30')
        self.assertEqual(len(result['4-hour']), 1)

    def test_checksum_fails_closed(self):
        self.load()
        (self.root/'raw.json').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            load_benchmarks('2026-10-30','2026-10-30','2026-10-30T16:00:00+05:30', self.root)


if __name__ == '__main__':
    unittest.main()
