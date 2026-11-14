"""Explicit local receipt bridge into the existing market and scraper monitors."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path


def snapshot(config_path=None, now=None):
    config_path=config_path or os.environ.get('SCRAPER_MARKET_LIVE_CONFIG')
    if not config_path:
        return dict(status='UNKNOWN',reason='Independent market service is not configured',
                    historical_acquisition='UNKNOWN',filing_detection='UNKNOWN',document_links='UNKNOWN')
    try:
        from ops.market_live.service import status
        result=status(Path(config_path),now=now or datetime.now(timezone.utc))
        config=json.loads(Path(config_path).read_text())
        result['historical_acquisition']=dict(status='UNKNOWN',conflict=result.get('refresh',{}).get('result',{}).get('reason'))
        path=config.get('document_freshness_report_path')
        report=json.loads(Path(path).read_text()) if path else None
        result['document_acquisition']=report or dict(status='UNKNOWN',reason='No explicit document timing report selected')
        return result
    except (OSError,ValueError,KeyError,TypeError):
        return dict(status='UNKNOWN',reason='Market freshness receipts unavailable or invalid')
