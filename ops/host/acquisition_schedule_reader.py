"""Fresh read-only schedule inspection of the deployed release; no source access."""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys

def snapshot(now=None):
    import automatic_acquisition as dispatcher
    from phase4_schedule import settings as document_settings, authorization_state
    from step3_schedule import settings as financial_settings
    now = now or datetime.now(timezone.utc)
    docs = document_settings()
    end = datetime.fromisoformat(docs['expires_at_utc'])
    slots = []
    for name, exchange, trigger in dispatcher.jobs():
        at = trigger.get_next_fire_time(None, now + timedelta(seconds=1))
        if exchange.startswith('DOCUMENT_') and docs.get('authorization_mode','finite')=='finite' and at >= end:
            continue
        slots.append({'job': name, 'exchange': exchange, 'next_at_ist': at.isoformat(), 'trigger': str(trigger)})
    slots.sort(key=lambda r: r['next_at_ist'])
    return {'jobs': slots, 'next_scheduled_batch': slots[0] if slots else None,
        'financial': financial_settings(), 'document_expires_at_utc': end.isoformat(),
        'document_window_expired': authorization_state(docs,now)=='EXPIRED_AUTHORIZATION',
        'document_authorization_state':authorization_state(docs,now),
        'document_authorization_mode':docs.get('authorization_mode','finite'),
        'document_verification_window_expired':now>=end,
        'calendar_catchup': False, 'slot_replay': False}


if __name__ == '__main__':
    release = Path(sys.argv[1])
    sys.path[:0] = [str(release/'ops/activation'), str(release)]
    print(json.dumps(snapshot(datetime.fromisoformat(sys.argv[2]))))
