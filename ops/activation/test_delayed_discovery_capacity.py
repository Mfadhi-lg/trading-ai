"""Discovery scaling retains durable per-day request reservations."""
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import automatic_acquisition as dispatcher
import phase4_schedule as schedule


def test_daily_request_budget_survives_batch_size_change(tmp_path):
    config={'discovery_limit':10,'screener_request_limit':10,'official_request_limit':0,
            'discovery_daily_slots':24,'discovery_requests_max_per_day':16,
            'discovery_hours':','.join(map(str,range(24)))}
    now=datetime(2026,10,30,5,tzinfo=timezone.utc)
    with patch.object(dispatcher,'STATE',tmp_path),patch.object(schedule,'settings',return_value=config):
        first=tmp_path/'2026-10-30-discovery-1';first.mkdir()
        assert schedule.reserve_daily_stage(first,'DOCUMENT_DISCOVERY',now)
        receipt=json.loads((first/'daily-budget-reservation.json').read_text())
        assert receipt['source_request_units']==10
        second=tmp_path/'2026-10-30-discovery-2';second.mkdir()
        assert not schedule.reserve_daily_stage(second,'DOCUMENT_DISCOVERY',now)
        config['screener_request_limit']=6
        assert schedule.reserve_daily_stage(second,'DOCUMENT_DISCOVERY',now)
        third=tmp_path/'2026-10-30-discovery-3';third.mkdir()
        assert not schedule.reserve_daily_stage(third,'DOCUMENT_DISCOVERY',now)


def test_capacity_rejects_invalid_or_unbounded_inputs():
    for bad in ({'screener_request_limit':101}, {'discovery_daily_slots':25},
                {'discovery_hours':'*'}, {'discovery_hours':'0,0'},
                {'discovery_requests_max_per_day':43}, {'official_request_limit':3}):
        with pytest.raises(ValueError):schedule.discovery_capacity(bad)
