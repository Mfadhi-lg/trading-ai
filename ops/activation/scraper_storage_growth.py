"""Durable scraper-store growth, independent of unrelated APFS consumption.

Growth charging is retained for observation; enforcement can be disabled explicitly.
Caller MUST hold the existing acquisition owner lock and supply a fresh verified
physical-volume preflight. No deletion/shrink refunds budget. Missing ledger
never silently establishes a new baseline after initial installation.
"""
from datetime import datetime
import json
import os
from pathlib import Path
import stat
import sys
import errno


def portable_inventory(root):
    result = {}
    def visit(folder):
        with os.scandir(folder) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    raise RuntimeError('Storage accounting refuses symlink: '+entry.path)
                if stat.S_ISDIR(info.st_mode):
                    visit(entry.path)
                elif stat.S_ISREG(info.st_mode):
                    result[str(Path(entry.path).relative_to(root))] = (info.st_size,info.st_mtime)
    visit(root)
    return result


def inventory(root):
    if sys.platform=='darwin':
        from macos_inventory import inventory as bulk_inventory
        try:
            return bulk_inventory(root)
        except OSError as exc:
            if exc.errno not in (errno.ENOSYS,errno.ENOTSUP,errno.EOPNOTSUPP):
                raise
    return portable_inventory(root)


def account(root, ledger, config, capacity, *, bootstrap=False, reserve_bytes=0):
    root, ledger = Path(root), Path(ledger)
    if not root.is_dir() or capacity['root'] != str(root):
        raise RuntimeError('Storage accounting requires the verified document root')
    identity = {'root':str(root),'volume_uuid':capacity['volume_uuid'],
                'physical_identity_sha256':capacity['physical_identity_sha256'],
                'activated_at_utc':config['activated_at_utc'],
                'limit_bytes':config['external_growth_limit_bytes']}
    if capacity['free_bytes']-capacity['required_free_bytes'] < reserve_bytes:
        raise RuntimeError('Reserve cannot cover document slot')
    if ledger.exists():
        saved = json.loads(ledger.read_text())
        if saved['identity'] != identity:
            raise RuntimeError('Storage growth ledger identity/configuration changed')
    elif bootstrap:
        saved = {'version':1,'identity':identity,'charged_bytes':0,'files':{},
                 'bootstrap_basis':'Conservative full size of store files modified since retained activation; older files retain current-size baseline. No free-space delta.'}
    else:
        raise RuntimeError('Storage growth ledger missing; explicit initial inventory required')
    epoch = datetime.fromisoformat(config['activated_at_utc']).timestamp()
    current = inventory(root)
    for name,(size,modified) in current.items():
        old = saved['files'].get(name)
        if old is None:
            # First inventory conservatively charges entire recently modified
            # files, including metadata/exports, rather than resetting to zero.
            baseline = size if bootstrap and modified < epoch else 0
            old = {'maximum_bytes':baseline}
            saved['files'][name] = old
        saved['charged_bytes'] += max(0,size-old['maximum_bytes'])
        old['maximum_bytes'] = max(size,old['maximum_bytes'])
    from automatic_acquisition import save
    save(ledger,saved)  # fsync file + atomic replace + parent fsync
    result = {k:v for k,v in saved.items() if k!='files'}
    result.update(observed_files=len(current),retained_paths=len(saved['files']),reserved_bytes=reserve_bytes,
                  remaining_bytes=identity['limit_bytes']-saved['charged_bytes'])
    enforced = config.get('enforce_external_growth_limit', True)
    result['budget_enforced'] = enforced
    if not enforced:
        result['remaining_bytes'] = None
    if enforced and saved['charged_bytes']+reserve_bytes>identity['limit_bytes']:
        raise RuntimeError('Scraper-attributed external growth budget exhausted')
    return result


def batch_account(root, ledger, config, capacity, *, reserve_bytes=0):
    """Scan only when growth enforcement needs it; capacity is always checked.

    Observational inventories belong in explicit maintenance, not every batch.
    account() remains available for a full inventory with enforcement disabled.
    """
    if config.get('enforce_external_growth_limit', True):
        return account(root, ledger, config, capacity, reserve_bytes=reserve_bytes)
    root = Path(root)
    if not root.is_dir() or capacity['root'] != str(root):
        raise RuntimeError('Storage accounting requires the verified document root')
    if capacity['free_bytes']-capacity['required_free_bytes'] < reserve_bytes:
        raise RuntimeError('Reserve cannot cover document slot')
    return {'budget_enforced': False, 'inventory_deferred': True,
            'reason': 'Growth enforcement disabled; full inventory is maintenance only',
            'reserved_bytes': reserve_bytes, 'remaining_bytes': None}


def account_and_refresh(root, ledger, config, capacity, *, reserve_bytes=0):
    """Keep growth accounting, then remeasure the device before starting work."""
    growth = batch_account(root, ledger, config, capacity, reserve_bytes=reserve_bytes)
    if growth.get('inventory_deferred'):
        # The supplied preflight is already fresh: no scan has delayed it.
        return growth, capacity
    from phase4_documents import storage_preflight
    fresh = storage_preflight()
    for key in ('root', 'volume_uuid', 'physical_identity_sha256'):
        if fresh[key] != capacity[key]:
            raise RuntimeError('Physical document identity changed during storage accounting: '+key)
    if fresh['free_bytes']-fresh['required_free_bytes'] < reserve_bytes:
        raise RuntimeError('Reserve cannot cover document slot')
    return growth, fresh


if __name__ == '__main__':
    import argparse
    import fcntl
    parser = argparse.ArgumentParser(description='Run an explicit full storage-growth inventory')
    parser.add_argument('--audit', action='store_true', required=True)
    parser.parse_args()
    from automatic_acquisition import HERE, STATE
    from phase4_documents import STORE, storage_preflight
    from phase4_schedule import settings
    with (HERE / '.scheduler-admission.lock').open('a') as admission:
        fcntl.flock(admission, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (HERE / '.nse-owner.lock').open('a') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print(json.dumps(account(STORE, STATE/'scraper-storage-growth-v1.json',
                                     settings(), storage_preflight())))
