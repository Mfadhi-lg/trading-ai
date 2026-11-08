import json
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import automatic_acquisition as dispatcher
import phase4_schedule as schedule


def test_all_lanes_share_daily_request_and_byte_budget(tmp_path):
    config=json.loads((Path(__file__).parent/'phase4_schedule.json').read_text())
    now=datetime(2026,10,30,12,tzinfo=timezone.utc)
    with patch.object(dispatcher,'STATE',tmp_path),patch.object(schedule,'settings',return_value=config):
        for kind,count in [('DOCUMENT_DISCOVERY',config.get('discovery_daily_slots',4)),('DOCUMENT_DOWNLOAD',config.get('download_daily_slots',24)),('DOCUMENT_RATING',config.get('rating_daily_slots',1)),('DOCUMENT_PATROL',1)]:
            for i in range(count):
                folder=tmp_path/f'2026-10-30-{kind}-{i}';folder.mkdir()
                assert schedule.reserve_daily_stage(folder,kind,now)
            extra=tmp_path/f'2026-10-30-{kind}-extra';extra.mkdir()
            assert not schedule.reserve_daily_stage(extra,kind,now)
    reservations=[json.loads(p.read_text()) for p in tmp_path.glob('*/daily-budget-reservation.json')]
    assert sum(r['source_request_units'] for r in reservations)==config['source_requests_max_per_day']
    assert sum(r['source_byte_units'] for r in reservations)==config['source_bytes_max_per_day']
