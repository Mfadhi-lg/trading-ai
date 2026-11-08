"""Human-readable terminal rendering of collected and derived trading data.

All functions are read-only. They take an open sqlite3.Connection and return a
block of text ready to print, so the CLI layer stays a thin dispatch wrapper.

Financial sections are rendered as full period matrices (line items down, one
column per reporting period) so quarter-on-quarter and year-on-year movement is
readable at a glance, matching how the source presents them.
"""

from __future__ import annotations

from datetime import datetime
import re
import shutil
import sqlite3

# Statement sections keyed by the `section_code` written by the Screener
# parser, in the order the source presents them.
SECTION_LABELS: tuple[tuple[str, str], ...] = (
    ("quarters", "Quarterly Results"),
    ("pnl", "Profit & Loss"),
    ("balance_sheet", "Balance Sheet"),
    ("cash_flow", "Cash Flow"),
    ("ratios", "Ratios"),
)

SECTION_CODES: tuple[str, ...] = tuple(code for code, _ in SECTION_LABELS)

# Values that should hug the right edge of their column.
_NUMERIC = re.compile(r"^[₹$\s]*-?[\d,]+(\.\d+)?\s*(%|Cr\.?|Rs\.?)?$")

# Longest a line-item / metric label may grow before it is elided.
LABEL_WIDTH = 30

# Never squeeze a period matrix below this many columns, even on a narrow
# terminal -- a single column defeats the point of the view.
MIN_PERIOD_COLUMNS = 3

DOCUMENT_TYPE_LABELS: dict[str, str] = {
    "presentation": "Presentation / PPT",
    "transcript": "Concall transcript",
    "recording": "Concall recording",
    "credit_rating": "Credit rating",
}


def _date_from_document_url(url: str) -> str | None:
    """Extract a conservative YYYY-MM-DD hint from common document URLs."""

    candidates = (
        (r"(?:^|\D)((?:19|20)\d{6})", "%Y%m%d"),
        (r"(?:^|\D)([0-3]\d[01]\d(?:19|20)\d{2})", "%d%m%Y"),
    )
    for pattern, date_format in candidates:
        match = re.search(pattern, url)
        if match is None:
            continue
        try:
            return datetime.strptime(match.group(1), date_format).date().isoformat()
        except ValueError:
            continue
    return None


def _terminal_width(default: int = 120) -> int:
    return shutil.get_terminal_size(fallback=(default, 40)).columns


def _fit(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: max(1, width - 1)] + "…"


def _is_numeric(value: str) -> bool:
    return bool(_NUMERIC.match(value.strip())) if value.strip() else False


def render_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    title: str | None = None,
    max_width: int = 38,
) -> str:
    """Render an aligned ASCII table, right-aligning numeric columns."""
    header = [str(h) for h in headers]
    data = [[("" if c is None else str(c)) for c in row] for row in rows]

    widths = [len(h) for h in header]
    for row in data:
        for index, cell in enumerate(row):
            if index < len(widths):
                widths[index] = max(widths[index], len(cell))
    widths = [min(w, max_width) for w in widths]

    # A column is numeric when every populated cell in it looks like a number.
    right = []
    for index in range(len(header)):
        cells = [r[index] for r in data if index < len(r) and r[index].strip()]
        right.append(bool(cells) and all(_is_numeric(c) for c in cells))

    def _line(cells: list[str]) -> str:
        out = []
        for index, cell in enumerate(cells):
            if index >= len(widths):
                continue
            text = _fit(str(cell), widths[index])
            out.append(
                text.rjust(widths[index]) if right[index] else text.ljust(widths[index])
            )
        return "  ".join(out).rstrip()

    lines: list[str] = []
    if title:
        lines.append(title)
    # Headers of numeric columns align with their values.
    lines.append(
        "  ".join(
            _fit(h, widths[i]).rjust(widths[i]) if right[i] else _fit(h, widths[i]).ljust(widths[i])
            for i, h in enumerate(header)
        ).rstrip()
    )
    lines.append("  ".join("-" * w for w in widths))
    for row in data:
        lines.append(_line(row))
    return "\n".join(lines)


def _divider(text: str = "") -> str:
    return text or "-" * 72


def _select_periods(
    labels: list[str],
    columns: list[list[str]],
    *,
    periods: int,
    width: int,
) -> tuple[list[int], int]:
    """Choose which period columns to show, keeping the most recent ones.

    Returns the indexes to display and the total number available. `periods`
    of 0 means "every period"; a positive value forces that many; a negative
    value means "as many as fit the terminal".
    """
    total = len(labels)
    if periods > 0:
        keep = min(periods, total)
        return list(range(total - keep, total)), total
    if periods == 0:
        return list(range(total)), total

    # Auto: greedily take columns from the right until the terminal is full.
    budget = width - LABEL_WIDTH - 2
    chosen: list[int] = []
    for index in range(total - 1, -1, -1):
        cells = [labels[index], *columns[index]]
        needed = max((len(c) for c in cells), default=0) + 2
        if chosen and needed > budget:
            break
        budget -= needed
        chosen.append(index)
    while len(chosen) < min(MIN_PERIOD_COLUMNS, total):
        chosen.append(total - 1 - len(chosen))
    return sorted(chosen), total


def _matrix_block(
    label: str,
    item_labels: list[str],
    period_labels: list[str],
    values: list[list[str]],
    *,
    periods: int,
    unit_note: str = "",
) -> str:
    """Render one line-items x periods matrix."""
    columns = [[row[i] for row in values] for i in range(len(period_labels))]
    keep, total = _select_periods(period_labels, columns, periods=periods, width=_terminal_width())

    headers = ["Item", *(period_labels[i] for i in keep)]
    rows = [
        [item, *(row[i] for i in keep)]
        for item, row in zip(item_labels, values)
    ]
    heading = f"{label}{unit_note}"
    if len(keep) < total:
        heading += f"   [showing last {len(keep)} of {total} periods; --periods 0 for all]"
    return "\n".join([_divider(), heading, render_table(headers, rows)])


# ---------------------------------------------------------------- company


def resolve_company(conn: sqlite3.Connection, term: str) -> dict | None:
    """Resolve a ticker (NSE), BSE code, or legal-name fragment to a company."""
    term = term.strip()
    row = conn.execute(
        """
        SELECT c.company_id, c.current_legal_name
        FROM companies c
        WHERE c.company_id IN (
            SELECT company_id FROM security_identifiers
            WHERE LOWER(identifier_value) = LOWER(?) AND is_current = 1
        )
        OR LOWER(c.current_legal_name) = LOWER(?)
        OR LOWER(c.current_legal_name) LIKE ?
        ORDER BY
            CASE
                WHEN c.company_id IN (
                    SELECT company_id FROM security_identifiers
                    WHERE LOWER(identifier_value) = LOWER(?) AND is_current = 1
                ) THEN 0
                WHEN LOWER(c.current_legal_name) = LOWER(?) THEN 1
                ELSE 2
            END,
            c.company_id
        LIMIT 1
        """,
        (term, term, f"%{term.lower()}%", term, term),
    ).fetchone()
    if row is None:
        return None
    company_id = int(row["company_id"])
    identifiers = conn.execute(
        """
        SELECT identifier_type, identifier_value
        FROM security_identifiers
        WHERE company_id=? AND is_current = 1
        ORDER BY identifier_type
        """,
        (company_id,),
    ).fetchall()
    nse = next((r["identifier_value"] for r in identifiers if r["identifier_type"] == "NSE"), "-")
    bse = next((r["identifier_value"] for r in identifiers if r["identifier_type"] == "BSE"), "-")
    return {"company_id": company_id, "name": row["current_legal_name"], "nse": nse, "bse": bse}


def _latest_parse(conn: sqlite3.Connection, company_id: int, scope: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT parse_id, accepted_at_utc FROM latest_accepted_pages
        WHERE company_id=? AND source='screener' AND reporting_scope=?
        ORDER BY accepted_at_utc DESC LIMIT 1
        """,
        (company_id, scope),
    ).fetchone()


def company_document_links(
    conn: sqlite3.Connection,
    company_id: int,
    *,
    per_type: int = 3,
) -> list[dict[str, str | int]]:
    """Return the newest distinct official/source document links by type."""

    if per_type < 1:
        return []
    rows = conn.execute(
        """
        WITH preferred_source AS (
          SELECT source.artifact_id, source.document_url,
                 source.discovered_from_source, source.source_position,
                 ROW_NUMBER() OVER (
                   PARTITION BY source.artifact_id
                   ORDER BY
                     CASE source.availability_status
                       WHEN 'available' THEN 0
                       WHEN 'unknown' THEN 1
                       ELSE 2
                     END,
                     source.is_primary_source DESC,
                     CASE source.discovered_from_source
                       WHEN 'company_ir' THEN 0
                       WHEN 'rating_agency' THEN 1
                       WHEN 'nse' THEN 2
                       WHEN 'bse' THEN 3
                       ELSE 4
                     END,
                     source.artifact_source_id
                 ) AS source_rank
            FROM artifact_sources AS source
           WHERE (source.document_url LIKE 'https://%'
                  OR source.document_url LIKE 'http://%')
             AND source.availability_status NOT IN (
               'not_offered', 'not_applicable', 'source_not_covered'
             )
        )
        SELECT artifact.artifact_id, artifact.artifact_type,
               artifact.title_raw, artifact.period_end,
               artifact.fiscal_year, artifact.fiscal_quarter,
               artifact.event_date, artifact.published_at_utc,
               preferred.document_url, preferred.discovered_from_source,
               preferred.source_position
          FROM document_artifacts AS artifact
          JOIN preferred_source AS preferred
            ON preferred.artifact_id=artifact.artifact_id
           AND preferred.source_rank=1
         WHERE artifact.company_id=?
           AND artifact.artifact_type IN (
             'presentation', 'transcript', 'recording', 'credit_rating'
           )
         ORDER BY
               CASE artifact.artifact_type
                 WHEN 'presentation' THEN 0
                 WHEN 'transcript' THEN 1
                 WHEN 'recording' THEN 2
                 WHEN 'credit_rating' THEN 3
                 ELSE 4
               END,
               CASE
                 WHEN artifact.period_end IS NOT NULL
                   OR (artifact.fiscal_year IS NOT NULL
                       AND artifact.fiscal_quarter IS NOT NULL)
                 THEN 0 ELSE 1
               END,
               artifact.period_end DESC,
               artifact.fiscal_year DESC,
               artifact.fiscal_quarter DESC,
               preferred.source_position,
               CASE
                 WHEN artifact.event_date IS NOT NULL
                   OR artifact.published_at_utc IS NOT NULL
                 THEN 0 ELSE 1
               END,
               COALESCE(
                 artifact.event_date,
                 substr(artifact.published_at_utc, 1, 10),
                 ''
               ) DESC,
               artifact.first_observed_at_utc DESC,
               artifact.artifact_id
        """,
        (company_id,),
    ).fetchall()

    selected: list[dict[str, str | int]] = []
    type_counts: dict[str, int] = {}
    seen_urls: set[str] = set()
    for row in rows:
        artifact_type = str(row["artifact_type"])
        url = str(row["document_url"])
        if url in seen_urls:
            continue
        seen_urls.add(url)
        type_counts[artifact_type] = type_counts.get(artifact_type, 0) + 1
        if type_counts[artifact_type] > per_type:
            continue
        if row["period_end"]:
            period = str(row["period_end"])
        elif row["fiscal_year"] and row["fiscal_quarter"]:
            period = f"FY{int(row['fiscal_year'])}-Q{int(row['fiscal_quarter'])}"
        elif row["event_date"]:
            period = str(row["event_date"])
        elif row["published_at_utc"]:
            period = str(row["published_at_utc"])[:10]
        else:
            period = _date_from_document_url(url) or "Period unresolved"
        selected.append(
            {
                "artifact_id": int(row["artifact_id"]),
                "artifact_type": artifact_type,
                "type_label": DOCUMENT_TYPE_LABELS[artifact_type],
                "period": period,
                "title": str(row["title_raw"]),
                "url": url,
                "source": str(row["discovered_from_source"]),
            }
        )
    return selected


def _documents_block(conn: sqlite3.Connection, company_id: int) -> str | None:
    documents = company_document_links(conn, company_id)
    if not documents:
        return None
    lines = [_divider(), "Documents & Concall Links"]
    previous_type = ""
    for document in documents:
        artifact_type = str(document["artifact_type"])
        if artifact_type != previous_type:
            lines.append(str(document["type_label"]))
            previous_type = artifact_type
        title = _fit(str(document["title"]), max(48, _terminal_width() - 6))
        lines.append(f"  {document['period']}  {title}")
        lines.append(f"  {document['url']}")
    return "\n".join(lines)


def _section_matrix(
    conn: sqlite3.Connection, parse_id: int, section: str
) -> tuple[list[str], list[str], list[list[str]]] | None:
    """Return (line items, period labels, value grid) for one statement section.

    Ordering follows `row_position` / `column_position` as captured from the
    source, which keeps trailing periods such as TTM in place -- they carry no
    `period_end`, so ordering by date would silently drop them.
    """
    rows = conn.execute(
        """
        SELECT line_item_raw, row_position, column_position,
               period_label_raw, display_value
        FROM financial_observations
        WHERE parse_id=? AND section_code=?
        ORDER BY row_position, column_position
        """,
        (parse_id, section),
    ).fetchall()
    if not rows:
        return None

    periods: dict[int, str] = {}
    items: dict[int, str] = {}
    cells: dict[tuple[int, int], str] = {}
    for row in rows:
        periods.setdefault(row["column_position"], row["period_label_raw"] or "")
        items.setdefault(row["row_position"], row["line_item_raw"])
        cells[(row["row_position"], row["column_position"])] = row["display_value"] or ""

    period_keys = sorted(periods)
    # Drop rows carrying no values at all (e.g. the source's "Raw PDF" link
    # row); the underlying links are captured in their own tables.
    item_keys = [
        k
        for k in sorted(items)
        if any(cells.get((k, c), "").strip() for c in period_keys)
    ]
    if not item_keys:
        return None
    period_labels = [periods[k] for k in period_keys]
    item_labels = [items[k] for k in item_keys]
    grid = [[cells.get((r, c), "") for c in period_keys] for r in item_keys]
    return item_labels, period_labels, grid


def _growth_block(conn: sqlite3.Connection, parse_id: int) -> str | None:
    """Render the compounded growth mini-tables as one matrix."""
    rows = conn.execute(
        """
        SELECT metric_label_raw, block_position, row_position,
               period_label_raw, display_value
        FROM growth_observations
        WHERE parse_id=?
        ORDER BY block_position, row_position
        """,
        (parse_id,),
    ).fetchall()
    if not rows:
        return None

    # Periods differ per block (TTM vs 1 Year vs Last Year), so the columns are
    # the union, ordered by where they appear inside their block.
    order: dict[str, int] = {}
    metrics: dict[int, str] = {}
    cells: dict[tuple[int, str], str] = {}
    for row in rows:
        period = row["period_label_raw"] or ""
        order.setdefault(period, row["row_position"])
        metrics.setdefault(row["block_position"], row["metric_label_raw"])
        cells[(row["block_position"], period)] = row["display_value"] or ""

    period_labels = sorted(order, key=lambda p: (order[p], p))
    block_keys = sorted(metrics)
    headers = ["Metric", *period_labels]
    data = [
        [metrics[b], *(cells.get((b, p), "") for p in period_labels)]
        for b in block_keys
    ]
    return "\n".join([_divider(), "Compounded Growth", render_table(headers, data)])


def _shareholding_block(
    conn: sqlite3.Connection, parse_id: int, frequency: str, periods: int
) -> str | None:
    rows = conn.execute(
        """
        SELECT holder_label_raw, row_position, column_position,
               period_label_raw, display_value
        FROM shareholding_observations
        WHERE parse_id=? AND frequency=?
        ORDER BY row_position, column_position
        """,
        (parse_id, frequency),
    ).fetchall()
    if not rows:
        return None
    period_map: dict[int, str] = {}
    holder_map: dict[int, str] = {}
    cells: dict[tuple[int, int], str] = {}
    for row in rows:
        period_map.setdefault(row["column_position"], row["period_label_raw"] or "")
        holder_map.setdefault(row["row_position"], row["holder_label_raw"])
        cells[(row["row_position"], row["column_position"])] = row["display_value"] or ""
    period_keys = sorted(period_map)
    holder_keys = sorted(holder_map)
    grid = [[cells.get((r, c), "") for c in period_keys] for r in holder_keys]
    return _matrix_block(
        f"Shareholding Pattern ({frequency})",
        [holder_map[k] for k in holder_keys],
        [period_map[k] for k in period_keys],
        grid,
        periods=periods,
    )


def _metrics_block(conn: sqlite3.Connection, parse_id: int) -> str:
    """Render every captured metric, laid out in three label/value pairs."""
    rows = conn.execute(
        """
        SELECT label_raw, display_value
        FROM company_metric_observations
        WHERE parse_id=?
        ORDER BY position, metric_observation_id
        """,
        (parse_id,),
    ).fetchall()
    if not rows:
        return "Key Metrics\n(no metrics captured)"

    pairs = [(r["label_raw"], r["display_value"] or "") for r in rows]
    per_row = 3 if _terminal_width() >= 100 else 2
    grid: list[list[str]] = []
    for start in range(0, len(pairs), per_row):
        chunk = pairs[start : start + per_row]
        cells: list[str] = []
        for label, value in chunk:
            cells.extend([label, value])
        while len(cells) < per_row * 2:
            cells.append("")
        grid.append(cells)

    headers = [""] * (per_row * 2)
    body = render_table(headers, grid)
    # Drop the blank header and rule that render_table emits for empty headers.
    body = "\n".join(body.split("\n")[2:])
    return f"Key Metrics ({len(pairs)})\n{body}"


def render_company(
    conn: sqlite3.Connection,
    term: str,
    *,
    scope: str | None = None,
    periods: int = -1,
    sections: list[str] | None = None,
    show_shareholding: bool = True,
) -> str:
    company = resolve_company(conn, term)
    if company is None:
        return f"No company matched '{term}'. Try a NSE ticker, BSE code, or name fragment."

    wanted = list(sections) if sections else list(SECTION_CODES)
    lines: list[str] = [f"{company['name']}  (NSE: {company['nse']}  BSE: {company['bse']})"]

    target_scope = scope or "consolidated"
    parse = _latest_parse(conn, company["company_id"], target_scope)
    if parse is None and scope is None:
        parse = _latest_parse(conn, company["company_id"], "standalone")
        if parse is not None:
            target_scope = "standalone"

    if parse is None:
        lines.append(f"No accepted Screener data on record for scope '{target_scope}'.")
        documents = _documents_block(conn, company["company_id"])
        if documents:
            lines.append(documents)
        return "\n".join(lines)
    parse_id = int(parse["parse_id"])

    quote = conn.execute(
        "SELECT price_display_value, currency FROM quote_observations WHERE parse_id=? LIMIT 1",
        (parse_id,),
    ).fetchone()
    if quote:
        lines.append(
            f"Current Price: {quote['price_display_value']} {quote['currency'] or ''}".rstrip()
        )
    lines.append(f"Scope: {target_scope}   Accepted: {parse['accepted_at_utc']}")
    lines.append("")
    lines.append(_metrics_block(conn, parse_id))

    for code, label in SECTION_LABELS:
        if code not in wanted:
            continue
        matrix = _section_matrix(conn, parse_id, code)
        if matrix is None:
            continue
        item_labels, period_labels, grid = matrix
        lines.append(_matrix_block(label, item_labels, period_labels, grid, periods=periods))
        if code == "pnl":
            growth = _growth_block(conn, parse_id)
            if growth:
                lines.append(growth)

    if show_shareholding:
        for frequency in ("quarterly", "yearly"):
            block = _shareholding_block(conn, parse_id, frequency, periods)
            if block:
                lines.append(block)

    documents = _documents_block(conn, company["company_id"])
    if documents:
        lines.append(documents)

    return "\n".join(lines)


# ---------------------------------------------------------------- mutual funds


def render_mf(
    conn: sqlite3.Connection,
    *,
    publisher: str | None,
    month: str | None,
    scope: str,
    isin: str | None,
    limit: int,
) -> str:
    params: list = []
    where: list[str] = []
    if publisher:
        where.append("f.publisher = ?")
        params.append(publisher)
    if month:
        where.append("f.disclosure_month = ?")
        params.append(month)
    if scope:
        where.append("f.feature_scope = ?")
        params.append(scope)
    if isin:
        where.append("f.isin_normalized = ?")
        params.append(isin)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"""
        SELECT isin, name, month, scope, holders,
               net_quantity_change, gross_quantity_bought, gross_quantity_sold,
               funds_increasing_quantity, funds_decreasing_quantity,
               complete_fund_exits, new_fund_entries, portfolio_weight_change
        FROM (
            SELECT f.isin_normalized AS isin, s.canonical_name AS name,
                   f.disclosure_month AS month, f.feature_scope AS scope,
                   f.active_fund_holder_count AS holders,
                   f.net_quantity_change, f.gross_quantity_bought,
                   f.gross_quantity_sold, f.funds_increasing_quantity,
                   f.funds_decreasing_quantity, f.complete_fund_exits,
                   f.new_fund_entries, f.portfolio_weight_change,
                   ROW_NUMBER() OVER (
                       PARTITION BY f.isin_normalized, f.disclosure_month,
                                    f.feature_scope
                       ORDER BY f.feature_set_id DESC
                   ) AS rn
            FROM mf_stock_monthly_features f
            LEFT JOIN mf_securities s ON s.security_id = f.security_id
            {where_sql}
        )
        WHERE rn = 1
        ORDER BY month DESC, isin
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    if not rows:
        return "No mutual-fund stock-month features matched the filters."
    return "\n".join(
        [
            _divider(),
            "Mutual fund stock-month features",
            render_table(
                [
                    "ISIN", "Security", "Month", "Scope", "Holders", "Net qty",
                    "Bought", "Sold", "+qty", "-qty", "Exits", "New", "Wt chg",
                ],
                [
                    [
                        r["isin"], r["name"] or "-", r["month"], r["scope"], str(r["holders"]),
                        r["net_quantity_change"], r["gross_quantity_bought"],
                        r["gross_quantity_sold"], str(r["funds_increasing_quantity"]),
                        str(r["funds_decreasing_quantity"]), str(r["complete_fund_exits"]),
                        str(r["new_fund_entries"]), (r["portfolio_weight_change"] or "-"),
                    ]
                    for r in rows
                ],
            ),
        ]
    )


# ---------------------------------------------------------------- coverage


def render_coverage(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """
        SELECT c.current_legal_name AS name,
               (SELECT identifier_value FROM security_identifiers si
                 WHERE si.company_id=c.company_id AND si.identifier_type='NSE'
                   AND si.is_current=1 LIMIT 1) AS nse,
               cs.latest_discovered_result_period AS discovered,
               cs.latest_accepted_result_period AS accepted
        FROM companies c
        LEFT JOIN screener_company_collection_state cs
          ON cs.company_id = c.company_id AND cs.reporting_scope='consolidated'
        ORDER BY c.current_legal_name
        """
    ).fetchall()
    lines: list[str] = [_divider("Screener coverage per company")]
    lines.append(
        render_table(
            ["Company", "Symbol", "Latest discovered", "Latest accepted"],
            [
                [r["name"], r["nse"] or "-", r["discovered"] or "-", r["accepted"] or "-"]
                for r in rows
            ],
        )
    )
    lines.append("")
    lines.append(_divider("Pipeline state"))
    queue = conn.execute(
        "SELECT status, count(*) AS n FROM screener_company_queue GROUP BY status ORDER BY n DESC"
    ).fetchall()
    if queue:
        lines.append(
            render_table(["Queue status", "Count"], [[r["status"], str(r["n"])] for r in queue])
        )
    else:
        lines.append("(no queue rows)")
    return "\n".join(lines)


# ---------------------------------------------------------------- browse


def render_tables(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'
        ORDER BY lower(name)
        """
    ).fetchall()
    return render_table(["Table / view"], [[r["name"]] for r in rows])


def table_literal(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def render_browse(
    conn: sqlite3.Connection, table: str, *, where: str | None, limit: int
) -> str:
    allowed = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type IN ('table', 'view') AND lower(name)=lower(?)
        """,
        (table,),
    ).fetchone()
    if allowed is None:
        return f"Unknown table or view: {table}"
    table = allowed["name"]
    where_clause = ""
    if where:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table_literal(table)})")}
        column = where.split()[0].strip().strip('"').strip("`").strip("'")
        if column not in columns:
            raise ValueError(
                f"Unknown column '{column}' in table '{table}'. "
                f"Known columns: {', '.join(sorted(columns))}"
            )
        where_clause = f" WHERE {where}"
    try:
        cursor = conn.execute(
            f"SELECT * FROM {table_literal(table)}{where_clause} LIMIT ?", (limit,)
        )
        rows = cursor.fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"Invalid browse query: {exc}") from exc
    if not rows:
        return f"Table '{table}' has no rows{(' for ' + where) if where else ''}."
    headers = [d[0] for d in cursor.description]
    data = [[("" if cell is None else str(cell)) for cell in row] for row in rows]
    return render_table(headers, data, title=f"{table} ({len(rows)} rows)")
