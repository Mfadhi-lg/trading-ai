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
