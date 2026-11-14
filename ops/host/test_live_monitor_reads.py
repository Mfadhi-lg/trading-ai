import sqlite3
from pathlib import Path
import acquisition_queries as queries
import acquisition_db_status as census


def test_live_reader_sees_committed_wal_while_writer_holds_transaction(tmp_path,monkeypatch):
    db=tmp_path/'live.db'
    writer=sqlite3.connect(db)
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('CREATE TABLE facts(value INTEGER)')
    writer.execute('INSERT INTO facts VALUES(1)');writer.commit()
    writer.execute('INSERT INTO facts VALUES(2)')
    monkeypatch.setattr(queries,'assert_safe_empty_wal',lambda p: (_ for _ in ()).throw(AssertionError('cold guard used')))
    def read(conn,*args,**kwargs):
        assert conn.execute('PRAGMA query_only').fetchone()[0]==1
        try:conn.execute('INSERT INTO facts VALUES(3)')
        except sqlite3.OperationalError:pass
        else:raise AssertionError('Reader allowed a write')
        return [r[0] for r in conn.execute('SELECT value FROM facts')]
    monkeypatch.setattr(queries,'query',read)
    try:
        assert queries.inspect(db,'fixture',live=True)==[1]
        writer.commit()
        assert queries.inspect(db,'fixture',live=True)==[1,2]
        result=census.census(db,live=True)
        assert result['leases']['status']=='NOT_CHECKED'
        assert writer.execute('SELECT count(*) FROM facts').fetchone()[0]==2
    finally:writer.close()


def test_last_snapshot_survives_a_busy_refresh(tmp_path,monkeypatch):
    import acquisition_observations as observations
    monkeypatch.setattr(observations.lifecycle.dispatcher,'STATE',tmp_path)
    monkeypatch.setattr(observations.lifecycle,'database_status',lambda *a,**k:{'companies':[{'company_id':7}]})
    first=observations.Backend().company('search',term='Fixture')
    def busy(*args,**kwargs):raise RuntimeError('Active owner has not drained')
    monkeypatch.setattr(observations.lifecycle,'database_status',busy)
    result=observations.Backend().company('search',term='Fixture')
    assert result['data']==first['data']
    assert result['updated_at']==first['updated_at']
    assert result['stale'] is True


def test_native_monitor_skips_sqlite_gate_and_bounds_startup(monkeypatch):
    import acquisition_live_reader as reader
    import postgresql_runtime as native
    import time,pytest
    monkeypatch.setattr(native,'enabled',lambda:True)
    calls=[]
    monkeypatch.setattr(reader,'main',lambda:calls.append('native'))
    monkeypatch.setattr(reader.Path,'open',lambda *_a,**_k:(_ for _ in ()).throw(AssertionError('SQLite gate opened')))
    reader.run_reader()
    assert calls==['native']
    monkeypatch.setattr(reader,'main',lambda:time.sleep(.2))
    with pytest.raises(TimeoutError,match='wall-clock budget'):
        reader.run_reader(seconds=.02)


def test_request_deadline_cannot_be_extended_per_statement(monkeypatch):
    import postgresql_runtime as native
    from types import SimpleNamespace
    from unittest.mock import Mock
    import pytest
    conn=SimpleNamespace(raw=SimpleNamespace(execute=Mock()))
    monkeypatch.setattr('scrapper.db.repository_backend.is_postgresql',lambda _:True)
    values=iter([10.,11.,12.,13.,14.])
    monkeypatch.setattr(native.time,'monotonic',lambda:next(values))
    with native.request_deadline(2):
        native.query_deadline(conn,100.)
        with pytest.raises(TimeoutError):native.query_deadline(conn,100.)
    assert conn.raw.execute.call_count==1
