"""Separate deployed acquisition code from the editable workspace.

Only code/configuration is copied. Queues, receipts, credentials and locks stay
at their existing locations. Deployment is performed under both existing locks.
"""
import json
import os
from pathlib import Path
import shutil


def workspace_root(root):
    root = Path(root)
    marker = root / '.acquisition-release.json'
    return Path(json.loads(marker.read_text())['workspace_root']) if marker.exists() else root


def active_root(root):
    workspace = workspace_root(root)
    pointer = workspace / 'artifacts/acquisition-releases/active.json'
    if not pointer.exists():
        return Path(root)
    release = Path(json.loads(pointer.read_text())['release_root'])
    if (release.parent != workspace / 'artifacts/acquisition-releases'
            or workspace_root(release) != workspace):
        raise RuntimeError('Invalid acquisition release location')
    return release


def prepare_release(workspace, release, *, source=None):
    workspace, release = Path(workspace).resolve(), Path(release).absolute()
    if release.parent != workspace / 'artifacts/acquisition-releases':
        raise ValueError('Release must live in artifacts/acquisition-releases')
    source_root=workspace if source is None else Path(source).resolve()
    if source_root==release or source_root.is_relative_to(release):
        raise ValueError('Release source cannot be inside its destination')
    release.mkdir(parents=True, exist_ok=False)
    ignore = shutil.ignore_patterns('__pycache__', '.venv', 'store', '*.pyc', '*.lock')
    for directory in ('scrapper', 'ops/activation', 'ops/host'):
        shutil.copytree(source_root / directory, release / directory, ignore=ignore)
    marker={'workspace_root':str(workspace)}
    if source is not None:marker['source_root']=str(source_root)
    (release / '.acquisition-release.json').write_text(json.dumps(marker)+'\n')
    for directory in ('artifacts', 'docs', 'ops/docker', '.git', 'experiments'):
        (release / directory).symlink_to(workspace / directory, target_is_directory=True)
    # Credentials remain live and private; they are never copied into releases.
    (release / '.screener_session').symlink_to(workspace / '.screener_session')
    for name in ('.scheduler-admission.lock', '.nse-owner.lock', '.monitor-readers.lock'):
        source = workspace / 'ops/activation' / name
        if not source.is_file():
            raise RuntimeError('Existing ownership lock missing: '+name)
        # Hard links preserve the existing lock inode and remain accessible
        # when this directory is bind-mounted at /activation in Docker.
        os.link(source, release / 'ops/activation' / name)
    return release


def activate_release(release):
    """Switch the existing LaunchAgent while idle; restore it on failure."""
    import fcntl
    import hashlib
    import os
    import plistlib
    import subprocess
    import automatic_acquisition as scheduler
    release = Path(release).resolve()
    workspace = workspace_root(release)
    if release.parent != workspace/'artifacts/acquisition-releases' or release == workspace:
        raise ValueError('Expected a prepared acquisition release')
    manifest = json.loads((release/'ops/activation/automatic-acquisition-manifest.json').read_text())
    for name, digest in manifest['files'].items():
        if scheduler.runtime_dependency(name) and hashlib.sha256((release/name).read_bytes()).hexdigest() != digest:
            raise RuntimeError('Prepared runtime differs from its manifest: '+name)
    pointer = workspace/'artifacts/acquisition-releases/active.json'
    destination = Path.home()/'Library/LaunchAgents'/(scheduler.LABEL+'.plist')
    target = 'gui/'+str(os.getuid())+'/'+scheduler.LABEL
    domain = 'gui/'+str(os.getuid())
    with (workspace/'ops/activation/.scheduler-admission.lock').open('a') as admission:
        fcntl.flock(admission, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (workspace/'ops/activation/.nse-owner.lock').open('a') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            old_pointer = json.loads(pointer.read_text()) if pointer.exists() else None
            old_plist = destination.read_bytes()
            loaded = subprocess.run(['launchctl', 'print', target], capture_output=True)
            if loaded.returncode:
                raise RuntimeError('Existing acquisition LaunchAgent is not loaded')
            def install(payload):
                temporary = destination.with_suffix('.plist.tmp')
                temporary.write_bytes(payload)
                os.replace(temporary, destination)
            try:
                subprocess.run(['launchctl', 'bootout', target], check=True)
                scheduler.save(pointer, {'release_root': str(release)})
                install(plistlib.dumps(scheduler.launchagent()))
                subprocess.run(['launchctl', 'bootstrap', domain, str(destination)], check=True)
            except Exception:
                # Bootstrap can fail after partially loading: unload before rollback.
                subprocess.run(['launchctl', 'bootout', target], capture_output=True)
                if old_pointer is None:
                    pointer.unlink(missing_ok=True)
                else:
                    scheduler.save(pointer, old_pointer)
                install(old_plist)
                subprocess.run(['launchctl', 'bootstrap', domain, str(destination)], check=True)
                raise
    return {'status': 'DEPLOYED', 'release_root': str(release), 'launchagent': str(destination)}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', type=Path)
    parser.add_argument('--activate', type=Path)
    parser.add_argument('--source', type=Path, help='Reviewed code checkout for preparation; runtime state stays in the workspace')
    args = parser.parse_args()
    if (args.prepare is None) == (args.activate is None):
        parser.error('Choose --prepare or --activate')
    if args.source is not None and args.prepare is None:parser.error('--source requires --prepare')
    if args.prepare:
        print(json.dumps({'prepared': str(prepare_release(Path(__file__).resolve().parents[2], args.prepare,source=args.source))}))
    else:
        print(json.dumps(activate_release(args.activate)))
