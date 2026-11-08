"""Rating refresh selection and metadata persistence; no queue or finance writes."""

def selected_targets(conn, *, recovery_run, failed_hosts):
    return [dict(row) for row in conn.execute('''
        SELECT DISTINCT a.company_id,j.company_name,sid.source_company_id,
            sid.source_symbol,sid.canonical_url
        FROM browser_rating_recovery_jobs j
        JOIN document_artifacts a USING(artifact_id)
        JOIN source_identifiers sid ON sid.company_id=a.company_id
            AND sid.source='screener' AND sid.mapping_status='verified'
        WHERE j.run_id=? AND j.status='FAILED'
            AND j.target_host IN (?,?,?)
        ORDER BY a.company_id
    ''', (recovery_run, *failed_hosts))]


def old_jobs(conn, *, recovery_run, company_id, failed_hosts):
    return [dict(r) for r in conn.execute('''
            SELECT j.browser_job_id,j.requested_url,j.target_host,a.event_date
            FROM browser_rating_recovery_jobs j
            JOIN document_artifacts a USING(artifact_id)
            WHERE j.run_id=? AND j.status='FAILED' AND a.company_id=?
                AND j.target_host IN (?,?,?)
        ''', (recovery_run, company_id, *failed_hosts))]


def attach_fetch_identity(conn, *, company_id, scope, run_id, blob_id):
    conn.execute('''UPDATE fetch_observations SET company_id=?,reporting_scope=?
        WHERE run_id=? AND blob_id=?''', (company_id, scope, run_id, blob_id))
    conn.commit()


def persist_pending_links(conn, *, blob_id, parse_id, scope, links):
    first = conn.execute('SELECT first_observed_at_utc FROM response_blobs WHERE blob_id=?',
                         (blob_id,)).fetchone()[0]
    with conn:
        conn.executemany('''INSERT INTO document_links
            (parse_id,reporting_scope,document_type,period_label_raw,title_raw,
             position,source_url,publisher,availability_status,first_observed_at_utc,
             source_published_at_utc,available_at_utc,availability_basis)
            VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?,'first_observed')''',
            [(parse_id,scope,l.document_type,l.period_label_raw,l.title_raw,l.position,
              l.source_url,l.publisher,l.availability_status,first,first) for l in links])
        conn.execute("UPDATE parse_artifacts SET parse_status='ok' WHERE parse_id=?", (parse_id,))
