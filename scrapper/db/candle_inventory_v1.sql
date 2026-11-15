CREATE TABLE IF NOT EXISTS requests(
          request_id TEXT PRIMARY KEY, company_id INTEGER, instrument_key TEXT,
          unit TEXT, interval INTEGER, from_date TEXT, to_date TEXT,
          status TEXT, attempts INTEGER DEFAULT 0, raw_sha256 TEXT,
          raw_path TEXT, received_at TEXT, http_status INTEGER, row_count INTEGER,
          error_kind TEXT, elapsed_seconds REAL);
        CREATE INDEX IF NOT EXISTS request_gaps ON requests(status,company_id,unit,interval,from_date);
        CREATE TABLE IF NOT EXISTS datasets(
          dataset_id TEXT PRIMARY KEY, request_id TEXT, company_id INTEGER,
          instrument_key TEXT, timeframe TEXT, definition TEXT, observation_status TEXT,
          first_start TEXT,last_start TEXT,row_count INTEGER,parquet_path TEXT,
          parquet_sha256 TEXT,bytes INTEGER,raw_sha256 TEXT,observed_at TEXT,
          completeness TEXT,corporate_action_status TEXT,write_seconds REAL);
        CREATE INDEX IF NOT EXISTS candle_lookup ON datasets(company_id,timeframe,definition,observed_at);
        CREATE INDEX IF NOT EXISTS dataset_request ON datasets(request_id,definition,raw_sha256);
        CREATE TABLE IF NOT EXISTS revisions(
          request_id TEXT, previous_sha256 TEXT, new_sha256 TEXT, observed_at TEXT,
          PRIMARY KEY(request_id,new_sha256));
        CREATE TABLE IF NOT EXISTS candle_observations(
          company_id INTEGER,instrument_key TEXT,timeframe TEXT,definition TEXT,
          start TEXT,end_upper_bound TEXT,observed_at TEXT,status TEXT,
          quality TEXT,row_sha256 TEXT,dataset_id TEXT,
          PRIMARY KEY(instrument_key,timeframe,definition,start,observed_at));
        CREATE INDEX IF NOT EXISTS observation_asof ON candle_observations(company_id,timeframe,definition,observed_at,start);
