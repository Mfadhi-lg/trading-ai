"""Real-time CLI monitoring dashboard for scraper pipeline execution."""

from __future__ import annotations
from ..db.repository_backend import statement, is_postgresql, execute, generated_id, atomic_repository, begin_immediate

import datetime
import json
import os
import sqlite3
import sys
import time
from typing import Any

# Try importing rich; fallback gracefully if not installed
try:
    import rich
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, TextColumn
    from rich.style import Style
    from rich.table import Table
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
DEFAULT_REFRESH_INTERVAL = 5.0
AVERAGE_REFRESH_INTERVAL = 60.0


def _format_time_ago(utc_str: str | None) -> str:
    if not utc_str:
        return "-"
    try:
        # Handle ISO strings like 2026-08-07T06:12:30 or 2026-08-07 06:12:30
        utc_str_clean = utc_str.replace("T", " ").split(".")[0]
        dt = datetime.datetime.strptime(utc_str_clean, "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        diff_sec = int((now - dt).total_seconds())
        if diff_sec < 0:
            return "just now"
        if diff_sec < 60:
            return f"{diff_sec}s ago"
        if diff_sec < 3600:
            return f"{diff_sec // 60}m {diff_sec % 60}s ago"
        return f"{diff_sec // 3600}h {(diff_sec % 3600) // 60}m ago"
    except Exception:
        return utc_str.split("T")[-1][:8] if "T" in utc_str else utc_str


def _clean_path(path: str | None) -> str:
    if not path:
        return "-"
    # Convert /company/RELIANCE/consolidated/ -> RELIANCE
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "company":
        return parts[1].upper()
    return path


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "N/A"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    rem_seconds = seconds % 60
    if minutes < 60:
        return f"{minutes}m {rem_seconds:.1f}s"
    hours = int(minutes // 60)
    rem_minutes = minutes % 60
    return f"{hours}h {rem_minutes}m {rem_seconds:.0f}s"


def _display_outcome(status: str, last_error_json: str | None) -> str:
    """Map terminal scope/data exclusions to dashboard skips.

    The queue keeps its historical terminal status, but the operator view must
    distinguish an intentionally excluded company from a broken scrape.
    """
    if status not in {"failed_terminal", "failed_retryable", "failed"}:
        return status
    try:
        message = str(json.loads(last_error_json or "{}").get("error", {}).get("message", "")).lower()
    except (TypeError, ValueError, AttributeError):
        message = ""
    if ("below screener market-cap scope floor" in message
            or "not above screener market-cap scope floor" in message):
        return "skipped_low_market_cap"
    if "quarterly results headers are missing" in message or "profit and loss headers are missing" in message:
        return "skipped_insufficient_data"
    return status


def _fetch_average_run_seconds(conn: sqlite3.Connection) -> float | None:
    """Return the average duration of completed queue attempts.

    This deliberately uses adjacent state transitions instead of a self-join
    over the entire event history.  The old query compared every ``running``
    event to later events (and then searched for intermediate events), which
    becomes disproportionately expensive on a long-running queue.
    """
    row = conn.execute(
        statement(conn, """
        WITH transitions AS (
            SELECT
                to_status,
                event_at_utc,
                LAG(to_status) OVER (
                    PARTITION BY queue_item_id
                    ORDER BY event_at_utc, queue_event_id
                ) AS previous_status,
                LAG(event_at_utc) OVER (
                    PARTITION BY queue_item_id
                    ORDER BY event_at_utc, queue_event_id
                ) AS previous_event_at_utc
            FROM screener_company_queue_events
        )
        SELECT AVG(
            strftime('%s', event_at_utc) - strftime('%s', previous_event_at_utc)
        ) AS avg_sec
        FROM transitions
        WHERE previous_status = 'running'
          AND to_status IN ('accepted', 'failed_terminal', 'failed_retryable', 'manual_review')
        """, "\n        WITH transitions AS (\n            SELECT\n                to_status,\n                event_at_utc,\n                LAG(to_status) OVER (\n                    PARTITION BY queue_item_id\n                    ORDER BY event_at_utc, queue_event_id\n                ) AS previous_status,\n                LAG(event_at_utc) OVER (\n                    PARTITION BY queue_item_id\n                    ORDER BY event_at_utc, queue_event_id\n                ) AS previous_event_at_utc\n            FROM screener_company_queue_events\n        )\n        SELECT AVG(\n            EXTRACT(EPOCH FROM event_at_utc::timestamptz) - EXTRACT(EPOCH FROM previous_event_at_utc::timestamptz)\n        ) AS avg_sec\n        FROM transitions\n        WHERE previous_status = 'running'\n          AND to_status IN ('accepted', 'failed_terminal', 'failed_retryable', 'manual_review')\n        ")
    ).fetchone()
    return float(row["avg_sec"]) if row and row["avg_sec"] is not None else None


def fetch_monitor_data(
    conn: sqlite3.Connection, *, include_avg_seconds: bool = True
) -> dict[str, Any]:
    """Fetch live pipeline status, active workers, queue counts and recent activity."""
    # 1. Queue Status Counts
    queue_rows = conn.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM screener_company_queue
        GROUP BY status
        """
    ).fetchall()
    counts = {row["status"]: row["count"] for row in queue_rows}
    display_counts = dict(counts)

    # Only failed rows can have a specialised display outcome.  Avoid reading
    # and JSON-decoding every queue row on every dashboard refresh.
    outcome_rows = conn.execute(
        """
        SELECT status, last_error_json
        FROM screener_company_queue
        WHERE status IN ('failed', 'failed_retryable', 'failed_terminal', 'manual_review')
        """
    ).fetchall()
    for row in outcome_rows:
        display_counts[row["status"]] = display_counts.get(row["status"], 0) - 1
        outcome = _display_outcome(row["status"], row["last_error_json"])
        display_counts[outcome] = display_counts.get(outcome, 0) + 1

    pending = counts.get("pending", 0)
    accepted = counts.get("accepted", 0)
    skipped_already = counts.get("skipped_already_accepted", 0)
    skipped_low_market_cap = display_counts.get("skipped_low_market_cap", 0)
    skipped_insufficient_data = display_counts.get("skipped_insufficient_data", 0)
    skipped = skipped_already + skipped_low_market_cap + skipped_insufficient_data
    claimed = counts.get("claimed", 0)
    running = counts.get("running", 0)
    failed_retryable = display_counts.get("failed_retryable", 0)
    failed_terminal = display_counts.get("failed_terminal", 0)
    manual_review = display_counts.get("manual_review", 0)
    failed_generic = display_counts.get("failed", 0)
    failed = failed_retryable + failed_terminal + manual_review + failed_generic
    total = sum(counts.values())

    completed = accepted + skipped + failed
    pct = (completed / total * 100.0) if total > 0 else 100.0

    # 2. Currently Fetching / Active Queue Items
    active_rows = conn.execute(
        """
        SELECT queue_item_id, company_path_normalized,
               result_period, status, attempt_count, claimed_at_utc, updated_at_utc
        FROM screener_company_queue
        WHERE status IN ('claimed', 'running')
        ORDER BY claimed_at_utc DESC, updated_at_utc DESC
        LIMIT 6
        """
    ).fetchall()

    active_items = [
        {
            "id": r["queue_item_id"],
            "symbol": _clean_path(r["company_path_normalized"]),
            "path": r["company_path_normalized"] or "-",
            "period": r["result_period"] or "-",
            "status": r["status"],
            "attempt": r["attempt_count"],
            "claimed_ago": _format_time_ago(r["claimed_at_utc"] or r["updated_at_utc"]),
        }
        for r in active_rows
    ]

    # 3. Recent Activity Stream
    recent_rows = conn.execute(
        """
        SELECT queue_item_id, company_path_normalized,
               result_period, status, last_error_json, updated_at_utc
        FROM screener_company_queue
        WHERE status IN ('accepted', 'skipped_already_accepted', 'failed', 'failed_retryable', 'failed_terminal', 'manual_review')
        ORDER BY updated_at_utc DESC
        LIMIT 8
        """
    ).fetchall()

    recent_items = [
        {
            "id": r["queue_item_id"],
            "symbol": _clean_path(r["company_path_normalized"]),
            "period": r["result_period"] or "-",
            "status": _display_outcome(r["status"], r["last_error_json"]),
            "time_ago": _format_time_ago(r["updated_at_utc"]),
        }
        for r in recent_rows
    ]

    # 4. Total Companies Discovered vs Accepted Coverage
    company_stats = conn.execute(
        """
        SELECT COUNT(DISTINCT c.company_id) AS total_companies,
               COUNT(DISTINCT cs.company_id) AS tracked_companies
        FROM companies c
        LEFT JOIN screener_company_collection_state cs ON cs.company_id = c.company_id
        """
    ).fetchone()

    total_companies = company_stats["total_companies"] if company_stats else 0
    tracked_companies = company_stats["tracked_companies"] if company_stats else 0

    # This history-wide statistic is useful, but it does not need to be
    # recalculated every dashboard tick.
    avg_seconds = _fetch_average_run_seconds(conn) if include_avg_seconds else None

    return {
        "counts": counts,
        "pending": pending,
        "accepted": accepted,
        "skipped": skipped,
        "skipped_already_accepted": skipped_already,
        "skipped_low_market_cap": skipped_low_market_cap,
        "skipped_insufficient_data": skipped_insufficient_data,
        "claimed": claimed,
        "running": running,
        "failed": failed,
        "failed_retryable": failed_retryable,
        "failed_terminal": failed_terminal,
        "manual_review": manual_review,
        "total": total,
        "completed": completed,
        "pct": pct,
        "active_items": active_items,
        "recent_items": recent_items,
        "total_companies": total_companies,
        "tracked_companies": tracked_companies,
        "avg_seconds": avg_seconds,
    }


def render_rich_monitor(
    data: dict[str, Any],
    start_time: float,
    frame_idx: int,
    initial_completed: int,
) -> Panel:
    """Build a stunning Rich console UI panel."""
    spinner_char = SPINNER_FRAMES[frame_idx % len(SPINNER_FRAMES)]
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    elapsed_sec = int(time.time() - start_time)
    elapsed_str = f"{elapsed_sec // 3600:02d}:{(elapsed_sec % 3600) // 60:02d}:{elapsed_sec % 60:02d}"

    session_delta = max(0, data["completed"] - initial_completed)
    rate_per_min = max(0.0, (session_delta / (elapsed_sec / 60.0))) if elapsed_sec > 2 else 0.0

    # Remaining time calculation
    remaining_items = data["pending"] + data["claimed"] + data["running"]
    if rate_per_min > 0 and remaining_items > 0:
        eta_minutes = int(remaining_items / rate_per_min)
        if eta_minutes < 60:
            eta_str = f"~{eta_minutes}m"
        else:
            eta_str = f"~{eta_minutes // 60}h {eta_minutes % 60}m"
    elif remaining_items == 0:
        eta_str = "Completed 🎉"
    else:
        eta_str = "Calculating..."

    # Determine overall status badge
    if data["running"] > 0 or data["claimed"] > 0:
        status_badge = f"[bold white on green]  ⚡ FETCHING ACTIVE ({spinner_char})  [/bold white on green]"
    elif data["pending"] > 0:
        status_badge = f"[bold black on yellow]  🟡 QUEUED ({data['pending']} PENDING)  [/bold black on yellow]"
    else:
        status_badge = "[bold white on blue]  🎉 ALL TASKS FINISHED  [/bold white on blue]"

    # --- Header Table ---
    header_table = Table.grid(expand=True)
    header_table.add_column(justify="left", ratio=2)
    header_table.add_column(justify="center", ratio=3)
    header_table.add_column(justify="right", ratio=2)
    header_table.add_row(
        Text("TRADING-AI PIPELINE", style="bold cyan"),
        Text.from_markup(status_badge),
        Text(f"🕒 {now_utc}", style="dim white"),
    )

    # --- Progress Bar Section ---
    completed = data["completed"]
    total = data["total"]
    pct = data["pct"]

    bar_len = 36
    filled_len = int(bar_len * (pct / 100.0))
    bar_str = "█" * filled_len + "░" * (bar_len - filled_len)

    progress_table = Table.grid(expand=True)
    progress_table.add_column(ratio=1)
    progress_table.add_row(
        Text.from_markup(
            f" [bold cyan][{bar_str}][/bold cyan]  [bold yellow]{pct:5.1f}%[/bold yellow]  "
            f"({completed:,} / {total:,} processed)"
        )
    )
    avg_time_str = _format_duration(data.get("avg_seconds"))
    progress_table.add_row(
        Text.from_markup(
            f" [dim]Speed:[/dim] [bold green]{rate_per_min:.1f} items/min[/bold green] | "
            f"[dim]Avg Time/Co:[/dim] [bold yellow]{avg_time_str}[/bold yellow] | "
            f"[dim]ETA:[/dim] [bold magenta]{eta_str}[/bold magenta] | "
            f"[dim]Session Elapsed:[/dim] [bold white]{elapsed_str}[/bold white] | "
            f"[dim]Session Fetched:[/dim] [bold green]+{session_delta}[/bold green]"
        )
    )

    # --- Queue Metrics Grid ---
    metrics_table = Table(title=None, box=None, expand=True, pad_edge=False)
    metrics_table.add_column("📥 Pending", justify="center", style="bold cyan")
    metrics_table.add_column("⚡ Running", justify="center", style="bold magenta")
    metrics_table.add_column("⏳ Claimed", justify="center", style="bold yellow")
    metrics_table.add_column("✅ Accepted", justify="center", style="bold green")
    metrics_table.add_column("⏭️ Skipped", justify="center", style="bold blue")
    metrics_table.add_column("❌ Failed", justify="center", style="bold red")

    fetching_sign = f"{spinner_char} FETCHING" if data["running"] > 0 else "RUNNING"
    metrics_table.add_row(
        f"{data['pending']:,}",
        f"{fetching_sign} ({data['running']})" if data["running"] else "0",
        f"{data['claimed']:,}",
        f"{data['accepted']:,}",
        f"{data['skipped']:,}",
        f"{data['failed']:,}",
    )

    # --- Currently Fetching Section ---
    fetching_table = Table(
        title=f"📡 Active Workers & Fetching Status ({len(data['active_items'])} active)",
        title_style="bold green",
        box=None,
        expand=True,
        show_header=True,
        header_style="bold dim",
    )
    fetching_table.add_column("Symbol / Company", style="bold white")
    fetching_table.add_column("Period", style="cyan")
    fetching_table.add_column("State", style="magenta")
    fetching_table.add_column("Claimed", style="yellow")
    fetching_table.add_column("Attempt", justify="right", style="dim")

    if data["active_items"]:
        for item in data["active_items"]:
            state_text = f"{spinner_char} FETCHING" if item["status"] == "running" else "CLAIMED"
            fetching_table.add_row(
                item["symbol"],
                item["period"],
                state_text,
                item["claimed_ago"],
                str(item["attempt"]),
            )
    else:
        fetching_table.add_row(
            "[dim]No active workers fetching at this moment[/dim]", "", "", "", ""
        )

    # --- Recent Activity Log ---
    activity_table = Table(
        title="📜 Recent Completions Feed",
        title_style="bold cyan",
        box=None,
        expand=True,
        show_header=True,
        header_style="bold dim",
    )
    activity_table.add_column("Time", style="dim white", width=12)
    activity_table.add_column("Company / Path", style="bold white")
    activity_table.add_column("Period", style="cyan")
    activity_table.add_column("Outcome Status", justify="center")

    if data["recent_items"]:
        for item in data["recent_items"]:
            if item["status"] == "accepted":
                badge = "[bold green]✅ ACCEPTED[/bold green]"
            elif item["status"] in {"skipped_already_accepted", "skipped_low_market_cap", "skipped_insufficient_data"}:
                badge = "[dim blue]⏭️ SKIPPED[/dim blue]"
            elif item["status"] == "failed_retryable":
                badge = "[bold yellow]⚠️ RETRYABLE[/bold yellow]"
            elif item["status"] == "manual_review":
                badge = "[bold magenta]🔍 REVIEW[/bold magenta]"
            else:
                badge = "[bold red]❌ FAILED[/bold red]"
            activity_table.add_row(
                item["time_ago"],
                item["symbol"],
                item["period"],
                badge,
            )
    else:
        activity_table.add_row("[dim]No completions recorded yet[/dim]", "", "", "")

    # Combine into main layout panel
    content = Group(
        header_table,
        Text("─" * 80, style="dim black"),
        progress_table,
        Text("─" * 80, style="dim black"),
        metrics_table,
        Text("─" * 80, style="dim black"),
        fetching_table,
        Text("─" * 80, style="dim black"),
        activity_table,
    )

    return Panel(
        content,
        title="[bold green]📡 LIVE SCRAPER PROCESS MONITOR[/bold green]",
        subtitle="[dim]Press [bold white]Ctrl+C[/bold white] to exit | Auto-updating real-time view[/dim]",
        border_style="cyan",
        padding=(1, 2),
    )


def render_ansi_fallback(
    data: dict[str, Any],
    start_time: float,
    frame_idx: int,
    initial_completed: int,
) -> str:
    """Fallback plain-ANSI screen renderer when Rich is unavailable."""
    spinner_char = SPINNER_FRAMES[frame_idx % len(SPINNER_FRAMES)]
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    elapsed_sec = int(time.time() - start_time)

    pct = data["pct"]
    bar_len = 30
    filled = int(bar_len * (pct / 100.0))
    bar_str = "█" * filled + "░" * (bar_len - filled)

    lines = []
    lines.append("\033[H\033[J")  # Clear screen and move cursor to top-left
    lines.append("════════════════════════════════════════════════════════════════════════════════")
    lines.append(f"  TRADING-AI REAL-TIME PIPELINE MONITOR   {spinner_char} FETCHING ACTIVE   {now_utc}")
    lines.append("════════════════════════════════════════════════════════════════════════════════")
    lines.append(f"  Progress: [{bar_str}] {pct:5.1f}%  ({data['completed']:,} / {data['total']:,})")
    lines.append(f"  Avg Time/Company: {_format_duration(data.get('avg_seconds'))}")
    lines.append("────────────────────────────────────────────────────────────────────────────────")
    lines.append(f"  Pending:  {data['pending']:<8}  Running: {data['running']:<8}  Claimed: {data['claimed']:<8}")
    lines.append(f"  Accepted: {data['accepted']:<8}  Skipped: {data['skipped']:<8}  Failed:  {data['failed']:<8}")
    lines.append("────────────────────────────────────────────────────────────────────────────────")
    lines.append(f"  📡 ACTIVE FETCHING WORKERS ({len(data['active_items'])}):")
    if data["active_items"]:
        for item in data["active_items"]:
            lines.append(
                f"     • {item['symbol']:<15} {item['period']:<8} "
                f"[{item['status'].upper()}] claimed {item['claimed_ago']}"
            )
    else:
        lines.append("     (no active fetches currently)")
    lines.append("────────────────────────────────────────────────────────────────────────────────")
    lines.append("  📜 RECENT ACTIVITY FEED:")
    if data["recent_items"]:
        for item in data["recent_items"][:5]:
            lines.append(
                f"     [{item['time_ago']:<8}] {item['symbol']:<15} -> {item['status']}"
            )
    lines.append("════════════════════════════════════════════════════════════════════════════════")
    lines.append("  Press Ctrl+C to exit monitor mode.")
    return "\n".join(lines)


def run_live_monitor(
    conn: sqlite3.Connection, refresh_interval: float = DEFAULT_REFRESH_INTERVAL
) -> None:
    """Run interactive real-time continuous monitoring dashboard loop."""
    start_time = time.time()
    frame_idx = 0

    # Grab initial completed count to measure session throughput accurately
    if refresh_interval <= 0:
        raise ValueError("refresh_interval must be greater than zero")

    init_data = fetch_monitor_data(conn)
    initial_completed = init_data["completed"]
    avg_seconds = init_data["avg_seconds"]
    next_average_refresh = time.monotonic() + AVERAGE_REFRESH_INTERVAL

    if HAS_RICH:
        console = Console()
        console.clear()
        try:
            with Live(
                render_rich_monitor(init_data, start_time, frame_idx, initial_completed),
                console=console,
                # ``Live`` otherwise redraws in a background loop even when
                # the data has not changed.  Explicit refreshes below cap the
                # terminal and Docker log work at the requested interval.
                auto_refresh=False,
                screen=False,
            ) as live:
                while True:
                    time.sleep(refresh_interval)
                    frame_idx += 1
                    refresh_average = time.monotonic() >= next_average_refresh
                    data = fetch_monitor_data(conn, include_avg_seconds=refresh_average)
                    if refresh_average:
                        avg_seconds = data["avg_seconds"]
                        next_average_refresh = time.monotonic() + AVERAGE_REFRESH_INTERVAL
                    else:
                        data["avg_seconds"] = avg_seconds
                    panel = render_rich_monitor(data, start_time, frame_idx, initial_completed)
                    live.update(panel, refresh=True)
        except KeyboardInterrupt:
            console.print("\n[bold yellow]Monitor stopped by user.[/bold yellow]")
    else:
        try:
            while True:
                refresh_average = time.monotonic() >= next_average_refresh
                data = fetch_monitor_data(conn, include_avg_seconds=refresh_average)
                if refresh_average:
                    avg_seconds = data["avg_seconds"]
                    next_average_refresh = time.monotonic() + AVERAGE_REFRESH_INTERVAL
                else:
                    data["avg_seconds"] = avg_seconds
                sys.stdout.write(render_ansi_fallback(data, start_time, frame_idx, initial_completed))
                sys.stdout.flush()
                time.sleep(refresh_interval)
                frame_idx += 1
        except KeyboardInterrupt:
            print("\nMonitor stopped by user.")
