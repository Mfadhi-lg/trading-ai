"""Read-only cold-start SQLite state check; never checkpoint or clear files.

Called under the existing exchange ownership locks, with the host owner gate
already passed. Accept absent/empty WAL and a valid empty WAL-index. Nonempty
WAL, journals, malformed state, another SQLite connection or drift fail closed.
SQLite itself manages sidecars when the later authorized connection is opened.
"""
import ctypes
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import struct
import sys


def snapshot(db):
    result={}
    for suffix in ('','-wal','-shm','-journal'):
        path=Path(str(db)+suffix)
        try:
            value=path.lstat()
        except FileNotFoundError:
            result[suffix or 'main']=None
            continue
        if not stat.S_ISREG(value.st_mode):
            raise RuntimeError('SQLite DB/sidecar is not a regular file: '+str(path))
        item={'device':value.st_dev,'inode':value.st_ino,'bytes':value.st_size,
              'mtime_ns':value.st_mtime_ns,'ctime_ns':value.st_ctime_ns}
        if suffix=='-shm':
            with path.open('rb') as handle:
                item['header_sha256']=hashlib.sha256(handle.read(136)).hexdigest()
        result[suffix or 'main']=item
    return result


class _Flock(ctypes.Structure):
    _fields_=[('type',ctypes.c_short),('whence',ctypes.c_short),
              ('start',ctypes.c_longlong),('length',ctypes.c_longlong),('pid',ctypes.c_int)]


def _lock_state(path,start,length):
    if sys.platform!='linux' or ctypes.sizeof(_Flock)!=32:
        raise RuntimeError('Cannot verify the production Linux SQLite lock layout')
    query=_Flock(fcntl.F_WRLCK,os.SEEK_SET,start,length,0)
    with path.open('rb') as handle:
        reply=fcntl.fcntl(handle.fileno(),fcntl.F_GETLK,bytes(query))
    found=_Flock.from_buffer_copy(reply)
    return {'path':str(path),'start':start,'length':length,
            'held':found.type!=fcntl.F_UNLCK,'pid':found.pid}


def _database_locks(db,has_shm):
    locks=[_lock_state(db,0x40000000,512)]
    if has_shm:
        shm=Path(str(db)+'-shm')
        locks.extend((_lock_state(shm,120,8),_lock_state(shm,128,1)))
    return locks


def _empty_index(shm):
    with shm.open('rb') as handle:
        header=handle.read(136)
    if len(header)!=136 or header[:48]!=header[48:96]:
        raise RuntimeError('Malformed or inconsistent SQLite WAL-index header')
    values=struct.unpack_from('=10I',header)
    version=values[0]
    mx_frame=values[4]
    backfill=struct.unpack_from('=I',header,96)[0]
    attempted=struct.unpack_from('=I',header,128)[0]
    checksum=(0,0)
    for i in range(0,10,2):
        a=(checksum[0]+values[i]+checksum[1]) & 0xffffffff
        b=(checksum[1]+values[i+1]+a) & 0xffffffff
        checksum=(a,b)
    if (version!=3007000 or header[12]!=1 or
            checksum!=struct.unpack_from('=2I',header,40)):
        raise RuntimeError('Uninitialized or invalid SQLite WAL-index header')
    if mx_frame or backfill or attempted:
        raise RuntimeError('WAL-index is not empty; requires SQLite recovery assessment')
    return {'mxFrame':mx_frame,'nBackfill':backfill,'nBackfillAttempted':attempted}


def _assert_safe_empty_wal(db):
    db=Path(db)
    before=snapshot(db)
    if before['main'] is None:
        raise RuntimeError('Existing SQLite database required')
    if before['-journal'] is not None:
        raise RuntimeError('Rollback journal present; requires SQLite recovery assessment')
    wal=before['-wal']
    if wal is not None and wal['bytes']:
        raise RuntimeError('Nonempty WAL; requires SQLite-aware checkpoint/recovery assessment')
    shm=before['-shm']
    if shm and (shm['bytes']<32768 or shm['bytes']%32768):
        raise RuntimeError('Malformed SQLite SHM size')
    locks=_database_locks(db,shm is not None)
    if any(lock['held'] for lock in locks):
        raise RuntimeError('Active SQLite connection/lock; cold-start state is not quiescent')
    index=_empty_index(Path(str(db)+'-shm')) if shm is not None else None
    if snapshot(db)!=before:
        raise RuntimeError('SQLite files changed during the sidecar check')
    return {'status':'SAFE_EMPTY_WAL','files':before,'kernel_locks':locks,
            'wal_index':index,'checkpoint_required':False,'sidecars_modified':False}


def assert_safe_empty_wal(db):
    # Cooperating UI readers drain briefly for the cold-start check. Unknown
    # connections still fail the unchanged kernel-lock and WAL checks below.
    gate=Path(__file__).with_name('.monitor-readers.lock')
    if not gate.exists():return _assert_safe_empty_wal(db)
    import time
    with gate.open('rb') as handle:
        end=time.monotonic()+20
        while True:
            try:
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=end:raise RuntimeError('Monitoring readers did not drain before cold-start check')
                time.sleep(.05)
        try:return _assert_safe_empty_wal(db)
        finally:fcntl.flock(handle,fcntl.LOCK_UN)


def prepare_writer_startup(db, *, expected_inode):
    """SQLite-managed checkpoint under the host writer owner and reader gate."""
    import sqlite3
    db=Path(db)
    if db.stat().st_ino!=expected_inode:raise RuntimeError('Production DB file identity mismatch')
    gate=Path(__file__).with_name('.monitor-readers.lock')
    with gate.open('rb') as handle:
        # Monitor queries are bounded. Never contend with an unknown writer.
        fcntl.flock(handle,fcntl.LOCK_EX)
        state=snapshot(db)
        if state['-journal'] is not None:raise RuntimeError('Rollback journal present; requires recovery assessment')
        if any(lock['held'] for lock in _database_locks(db,state['-shm'] is not None)):
            raise RuntimeError('Unknown active SQLite connection before writer startup')
        if state['-wal'] is not None and state['-wal']['bytes']:
            with sqlite3.connect('file:'+str(db)+'?mode=rw',uri=True,timeout=2) as conn:
                if conn.execute('PRAGMA quick_check(1)').fetchone()[0]!='ok':raise RuntimeError('SQLite integrity check failed')
                result=conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
                if tuple(result)!=(0,0,0):raise RuntimeError('SQLite checkpoint did not fully drain: '+str(result))
            conn.close()
        fcntl.flock(handle,fcntl.LOCK_UN)
