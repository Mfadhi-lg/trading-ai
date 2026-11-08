"""Reconcile only the dispatcher admission gate under both existing owner locks.

Does not pause, wake, bootstrap or stop production. Must be run between batches.
Original source and manifest are preserved for review before replacement.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from acquisition_lifecycle import ACTIVATION, ROOT, atomic, lock


def prepare_source(source):
    slot = "    STATE.mkdir(parents=True, exist_ok=True)\n    day = at.astimezone(IST).date().isoformat()"
    slot_gate = """    if (STATE / 'lifecycle-paused.json').exists():
        return {'job': name, 'status': 'lifecycle_paused', 'automatic_retry': False}
""" + slot
    tick = "        verify_config()\n        if (STATE / 'verify-desktop-once').exists():"
    tick_gate = """        if (STATE / 'lifecycle-paused.json').exists():
            return {'status': 'lifecycle_paused', 'automatic_retry': False}
""" + tick
    if source.count(slot) != 1 or source.count(tick) != 1 or 'lifecycle-paused.json' in source:
        raise RuntimeError('Dispatcher differs from reviewed gate insertion points')
    return source.replace(slot, slot_gate).replace(tick, tick_gate)


def main():
    import automatic_acquisition as dispatcher
    with ExitStack() as stack:
        stack.enter_context(lock(ACTIVATION / '.scheduler-admission.lock'))
        stack.enter_context(lock(ACTIVATION / '.nse-owner.lock'))
        dispatcher.verify_config()
        source = ACTIVATION / 'automatic_acquisition.py'
        manifest = ACTIVATION / 'automatic-acquisition-manifest.json'
        candidate = prepare_source(source.read_text())
        compile(candidate, str(source), 'exec')
        out = ROOT / 'artifacts/operational-recovery/gate-integration'
        out.mkdir(parents=True, exist_ok=False)
        (out / 'automatic_acquisition.before.py').write_bytes(source.read_bytes())
        (out / 'manifest.before.json').write_bytes(manifest.read_bytes())
        payload = json.loads(manifest.read_text())
        key = 'ops/activation/automatic_acquisition.py'
        before = payload['files'][key]
        after = hashlib.sha256(candidate.encode()).hexdigest()
        payload['files'][key] = after
        temporary = source.with_suffix('.lifecycle.tmp')
        temporary.write_text(candidate)
        # All dispatcher admissions and adapters are excluded across both
        # replacements. No tick can see a source/manifest intermediate state.
        temporary.replace(source)
        atomic(manifest, payload)
        dispatcher.verify_config()
        atomic(out / 'reconciliation.json', {'changed': key, 'before_sha256': before,
            'after_sha256': after, 'observed_at_utc': datetime.now(timezone.utc).isoformat(),
            'lifecycle_transition_performed': False, 'ingestion_logic_changed': False})
        print(json.dumps({'status': 'PASS', 'evidence': str(out)}))


if __name__ == '__main__':
    main()
