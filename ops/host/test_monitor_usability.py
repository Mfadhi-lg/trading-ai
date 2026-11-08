from io import StringIO
import pytest
from rich.console import Console
from acquisition_terminal import resolve_shell_command
from reporting_quarters import normalize
from acquisition_quarter_view import render


def test_monitor_alias_and_quarter_guidance():
    assert resolve_shell_command('/scraper-monitor')=='monitor'
    assert normalize('2026-09')=='2026-09'
    with pytest.raises(ValueError,match='2026-09'):normalize('2026-08')


def test_downloads_puts_blocker_and_quarter_first():
    data={'coverage':{},'acquisition':{'document_links_discovered':0,'unknown_period_documents_for_quarter_companies':82278},'downloads':[]}
    feed={'paused':False,'tick':{},'blockers':{'DOCUMENT_DOWNLOAD.stopped.json':{'exit_code':2,'error':'Production sentinel mismatch: /documents/.trading_ai_runtime_identity.json'}},'schedule':{'jobs':[]}}
    stream=StringIO();Console(file=stream,width=110).print(render(feed,{'quarter':'2025-09','quarter_data':data,'quarter_at':'2026-10-30T17:05:00+00:00'},'downloads',True))
    text=stream.getvalue()
    assert 'Document drive identity check failed' in text
    assert '2026-09' in text and '2025-09' in text
    assert '82,278' in text and '/monitor --quarter unknown' in text
    assert 'GLOBAL BLOCKER' not in text and 'None' not in text and 'screener_company_queue' not in text
