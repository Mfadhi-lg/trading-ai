import sqlite3
import pytest
from scrapper.db import screener_discovery_repo as repo


def test_full_pagination_required_and_duplicates_rejected(monkeypatch):
    c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
    c.executescript('''CREATE TABLE screener_screen_runs(screen_run_id INTEGER,page_number INTEGER,page_count INTEGER,result_count INTEGER,query_raw TEXT,query_role TEXT,validation_status TEXT,captured_row_count INTEGER);
    CREATE TABLE screener_result_candidates(screen_run_id INTEGER,screen_row_company_id TEXT,company_path_normalized TEXT,result_period TEXT);
    INSERT INTO screener_screen_runs VALUES(1,1,2,2,'query','screen','passed',1),(2,2,2,2,'query','screen','passed',1);
    INSERT INTO screener_result_candidates VALUES(1,'A','/company/A/','2026-09'),(2,'B','/company/B/','2026-09');''')
    calls=[]
    def enqueue(conn,sid,**kwargs):calls.append(sid);return {'new':1,'advanced':0,'unchanged':0,'stale':0,'reused':0}
    monkeypatch.setattr(repo,'_enqueue_screen_candidates',enqueue)
    with pytest.raises(ValueError,match='Incomplete screen pagination'):repo.enqueue_complete_screen_pages(c,[1],now_utc='now')
    assert calls==[]
    result=repo.enqueue_complete_screen_pages(c,[2,1],now_utc='now')
    assert result['captured_companies']==2 and result['enqueue']['new']==2 and calls==[1,2]
    calls.clear();c.execute("UPDATE screener_result_candidates SET screen_row_company_id='A'")
    with pytest.raises(ValueError,match='duplicate'):repo.enqueue_complete_screen_pages(c,[1,2],now_utc='now')
    assert calls==[]


def test_writer_checkpoint_preserves_committed_wal(tmp_path,monkeypatch):
    import sqlite_sidecar_guard as guard
    db=tmp_path/'db';writer=sqlite3.connect(db)
    writer.execute('PRAGMA journal_mode=WAL');writer.execute('CREATE TABLE facts(n INTEGER)');writer.commit()
    reader=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True)
    reader.execute('BEGIN');reader.execute('SELECT * FROM facts').fetchall()
    writer.execute('INSERT INTO facts VALUES(17)');writer.commit();writer.close();reader.close()
    gate=tmp_path/'.monitor-readers.lock';gate.touch()
    monkeypatch.setattr(guard,'__file__',str(tmp_path/'guard.py'))
    monkeypatch.setattr(guard,'_database_locks',lambda *args:[])
    guard.prepare_writer_startup(db,expected_inode=db.stat().st_ino)
    with sqlite3.connect(db) as result:assert result.execute('SELECT n FROM facts').fetchone()[0]==17
    with pytest.raises(RuntimeError,match='identity'):guard.prepare_writer_startup(db,expected_inode=-1)
