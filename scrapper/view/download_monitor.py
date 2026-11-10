"""Real-time CLI dashboard for queued document downloads."""

from __future__ import annotations
from ..db.repository_backend import statement, is_postgresql, execute, generated_id, atomic_repository, begin_immediate

import datetime
import sqlite3
import sys
import time
from typing import Any

try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    HAS_RICH = True
except ImportError:
    HAS_RICH = False

from .monitor import DEFAULT_REFRESH_INTERVAL, SPINNER_FRAMES, _format_time_ago


TERMINAL_STATUSES = {
    "SUCCEEDED",
    "TERMINAL_FAILURE",
    "INTERACTION_REQUIRED",
    "SKIPPED_ALREADY_HAVE_CONTENT",
    "SKIPPED_POLICY",
}


ALL_QUEUE_CTE = """
WITH raw_queue AS (
    SELECT 'credit_rating' AS queue_kind, download_job_id, artifact_id,
           requested_url, target_host, status, attempt_count,
           next_attempt_at_utc, lease_owner, lease_expires_at_utc,
           last_result_status, last_error, updated_at_utc
      FROM credit_rating_download_queue
    UNION ALL
    SELECT 'document' AS queue_kind, download_job_id, artifact_id,
           requested_url, target_host, status, attempt_count,
           next_attempt_at_utc, lease_owner, lease_expires_at_utc,
           last_result_status, last_error, updated_at_utc
      FROM artifact_download_queue
), ranked_queue AS (
    SELECT raw_queue.*,
           ROW_NUMBER() OVER (
               PARTITION BY artifact_id
               ORDER BY CASE status
                   WHEN 'SUCCEEDED' THEN 0
                   WHEN 'SKIPPED_ALREADY_HAVE_CONTENT' THEN 1
                   WHEN 'LEASED' THEN 2
                   WHEN 'PENDING' THEN 3
                   WHEN 'RETRYABLE_FAILURE' THEN 4
                   WHEN 'INTERACTION_REQUIRED' THEN 5
                   WHEN 'TERMINAL_FAILURE' THEN 6
                   ELSE 7
               END,
               updated_at_utc DESC,
               download_job_id DESC
           ) AS artifact_rank
      FROM raw_queue
), all_queue AS (
    SELECT queue_kind, download_job_id, artifact_id, requested_url,
           target_host, status, attempt_count, next_attempt_at_utc,
           lease_owner, lease_expires_at_utc, last_result_status,
           last_error, updated_at_utc
      FROM ranked_queue
     WHERE artifact_rank=1
)
"""


def _format_bytes(byte_count: int | None) -> str:
    if not byte_count:
        return "0 B"
    value = float(byte_count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def _short_error(value: str | None, limit: int = 72) -> str:
    text = " ".join((value or "").split())
    if not text:
        return "-"
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _format_time_until(utc_str: str | None) -> str:
    if not utc_str:
        return "-"
    try:
        target = datetime.datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        if target.tzinfo is None:
            target = target.replace(tzinfo=datetime.timezone.utc)
        seconds = max(
            0,
            int(
                (
                    target.astimezone(datetime.timezone.utc)
                    - datetime.datetime.now(datetime.timezone.utc)
                ).total_seconds()
            ),
        )
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}m"
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    except (TypeError, ValueError):
        return utc_str


def fetch_download_monitor_data(conn: sqlite3.Connection) -> dict[str, Any]:
    """Fetch queue, transfer, worker and publisher-health download metrics."""

    counts = {
        str(row["status"]): int(row["count"])
        for row in conn.execute(
            ALL_QUEUE_CTE
            + """SELECT status, COUNT(*) AS count
                   FROM all_queue GROUP BY status"""
        )
    }
    total = sum(counts.values())
    completed = sum(counts.get(status, 0) for status in TERMINAL_STATUSES)
    pending = counts.get("PENDING", 0)
    leased = counts.get("LEASED", 0)
    retryable = counts.get("RETRYABLE_FAILURE", 0)
    succeeded = counts.get("SUCCEEDED", 0)
    interaction_required = counts.get("INTERACTION_REQUIRED", 0)
    failed = counts.get("TERMINAL_FAILURE", 0)
    skipped = counts.get("SKIPPED_ALREADY_HAVE_CONTENT", 0) + counts.get(
        "SKIPPED_POLICY", 0
    )
    pct = completed / total * 100.0 if total else 100.0

    ready_row = conn.execute(
        ALL_QUEUE_CTE
        + statement(conn, """SELECT COUNT(*) AS count
             FROM all_queue AS queue
            WHERE queue.status IN ('PENDING', 'RETRYABLE_FAILURE')
              AND (queue.next_attempt_at_utc IS NULL
                   OR julianday(queue.next_attempt_at_utc) <= julianday('now'))
              AND (queue.queue_kind='document' OR NOT EXISTS (
                    SELECT 1 FROM credit_rating_host_policy AS policy
                     WHERE policy.host=queue.target_host
                       AND policy.automation_status='BLOCKED_TERMS'
              ))
              AND (queue.queue_kind='credit_rating' OR NOT EXISTS (
                    SELECT 1 FROM artifact_download_host_health AS health
                     WHERE health.host=queue.target_host
                       AND health.blocked_until_utc IS NOT NULL
                       AND julianday(health.blocked_until_utc) > julianday('now')
              ))
              AND (queue.queue_kind='document' OR NOT EXISTS (
                    SELECT 1 FROM credit_rating_host_health AS health
                     WHERE health.host=queue.target_host
                       AND health.blocked_until_utc IS NOT NULL
                       AND julianday(health.blocked_until_utc) > julianday('now')
              ))""", "SELECT COUNT(*) AS count\n             FROM all_queue AS queue\n            WHERE queue.status IN ('PENDING', 'RETRYABLE_FAILURE')\n              AND (queue.next_attempt_at_utc IS NULL\n                   OR (EXTRACT(EPOCH FROM queue.next_attempt_at_utc::timestamptz) / 86400) <= (EXTRACT(EPOCH FROM CURRENT_TIMESTAMP) / 86400))\n              AND (queue.queue_kind='document' OR NOT EXISTS (\n                    SELECT 1 FROM credit_rating_host_policy AS policy\n                     WHERE policy.host=queue.target_host\n                       AND policy.automation_status='BLOCKED_TERMS'\n              ))\n              AND (queue.queue_kind='credit_rating' OR NOT EXISTS (\n                    SELECT 1 FROM artifact_download_host_health AS health\n                     WHERE health.host=queue.target_host\n                       AND health.blocked_until_utc IS NOT NULL\n                       AND (EXTRACT(EPOCH FROM health.blocked_until_utc::timestamptz) / 86400) > (EXTRACT(EPOCH FROM CURRENT_TIMESTAMP) / 86400)\n              ))\n              AND (queue.queue_kind='document' OR NOT EXISTS (\n                    SELECT 1 FROM credit_rating_host_health AS health\n                     WHERE health.host=queue.target_host\n                       AND health.blocked_until_utc IS NOT NULL\n                       AND (EXTRACT(EPOCH FROM health.blocked_until_utc::timestamptz) / 86400) > (EXTRACT(EPOCH FROM CURRENT_TIMESTAMP) / 86400)\n              ))")
    ).fetchone()
    ready = int(ready_row["count"]) if ready_row else 0

    active_rows = conn.execute(
        ALL_QUEUE_CTE
        + """SELECT queue.queue_kind, queue.download_job_id, queue.artifact_id,
                  queue.target_host,
                  queue.attempt_count, queue.lease_owner,
                  queue.lease_expires_at_utc, queue.updated_at_utc,
                  artifact.artifact_type, artifact.title_raw,
                  company.current_legal_name
             FROM all_queue AS queue
             JOIN document_artifacts AS artifact
               ON artifact.artifact_id=queue.artifact_id
             JOIN companies AS company ON company.company_id=artifact.company_id
            WHERE queue.status='LEASED'
            ORDER BY queue.updated_at_utc DESC, queue.download_job_id
            LIMIT 6"""
    ).fetchall()
    active_items = [
        {
            "id": int(row["download_job_id"]),
            "queue_kind": row["queue_kind"],
            "artifact_id": int(row["artifact_id"]),
            "artifact_type": row["artifact_type"],
            "company": row["current_legal_name"],
            "title": row["title_raw"],
            "host": row["target_host"] or "-",
            "attempt": int(row["attempt_count"]),
            "worker": row["lease_owner"] or "-",
            "updated_ago": _format_time_ago(row["updated_at_utc"]),
            "lease_expires": _format_time_ago(row["lease_expires_at_utc"]),
        }
        for row in active_rows
    ]

    host_rows = conn.execute(
        ALL_QUEUE_CTE
        + statement(conn, """, queue_counts AS (
               SELECT target_host AS host,
                      SUM(CASE WHEN status IN ('PENDING', 'RETRYABLE_FAILURE')
                               THEN 1 ELSE 0 END) AS queued,
                      SUM(CASE WHEN status='LEASED' THEN 1 ELSE 0 END) AS active,
                      SUM(CASE WHEN status='SUCCEEDED' THEN 1 ELSE 0 END) AS succeeded,
                      SUM(CASE WHEN status IN (
                                   'TERMINAL_FAILURE', 'INTERACTION_REQUIRED',
                                   'SKIPPED_POLICY'
                               ) THEN 1 ELSE 0 END) AS blocked
                 FROM all_queue
                GROUP BY target_host
             ), hosts AS (
               SELECT host FROM queue_counts
               UNION SELECT host FROM credit_rating_host_health
               UNION SELECT host FROM credit_rating_host_policy
             )
             SELECT hosts.host,
                    COALESCE(queue_counts.queued, 0) AS queued,
                    COALESCE(queue_counts.active, 0) AS active,
                    COALESCE(queue_counts.succeeded, 0) AS succeeded,
                    COALESCE(queue_counts.blocked, 0) AS blocked,
                    policy.automation_status, policy.max_concurrency,
                    health.blocked_until_utc, health.last_http_status,
                    health.consecutive_blocks,
                    CASE WHEN health.blocked_until_utc IS NOT NULL
                              AND julianday(health.blocked_until_utc) > julianday('now')
                         THEN 1 ELSE 0 END AS circuit_open
               FROM hosts
               LEFT JOIN queue_counts USING (host)
               LEFT JOIN credit_rating_host_policy AS policy USING (host)
               LEFT JOIN credit_rating_host_health AS health USING (host)
              WHERE COALESCE(queue_counts.queued, 0) > 0
                 OR COALESCE(queue_counts.active, 0) > 0
                 OR COALESCE(queue_counts.succeeded, 0) > 0
                 OR COALESCE(queue_counts.blocked, 0) > 0
              ORDER BY active DESC, queued DESC, hosts.host
              LIMIT 10""", ", queue_counts AS (\n               SELECT target_host AS host,\n                      SUM(CASE WHEN status IN ('PENDING', 'RETRYABLE_FAILURE')\n                               THEN 1 ELSE 0 END) AS queued,\n                      SUM(CASE WHEN status='LEASED' THEN 1 ELSE 0 END) AS active,\n                      SUM(CASE WHEN status='SUCCEEDED' THEN 1 ELSE 0 END) AS succeeded,\n                      SUM(CASE WHEN status IN (\n                                   'TERMINAL_FAILURE', 'INTERACTION_REQUIRED',\n                                   'SKIPPED_POLICY'\n                               ) THEN 1 ELSE 0 END) AS blocked\n                 FROM all_queue\n                GROUP BY target_host\n             ), hosts AS (\n               SELECT host FROM queue_counts\n               UNION SELECT host FROM credit_rating_host_health\n               UNION SELECT host FROM credit_rating_host_policy\n             )\n             SELECT hosts.host,\n                    COALESCE(queue_counts.queued, 0) AS queued,\n                    COALESCE(queue_counts.active, 0) AS active,\n                    COALESCE(queue_counts.succeeded, 0) AS succeeded,\n                    COALESCE(queue_counts.blocked, 0) AS blocked,\n                    policy.automation_status, policy.max_concurrency,\n                    health.blocked_until_utc, health.last_http_status,\n                    health.consecutive_blocks,\n                    CASE WHEN health.blocked_until_utc IS NOT NULL\n                              AND (EXTRACT(EPOCH FROM health.blocked_until_utc::timestamptz) / 86400) > (EXTRACT(EPOCH FROM CURRENT_TIMESTAMP) / 86400)\n                         THEN 1 ELSE 0 END AS circuit_open\n               FROM hosts\n               LEFT JOIN queue_counts USING (host)\n               LEFT JOIN credit_rating_host_policy AS policy USING (host)\n               LEFT JOIN credit_rating_host_health AS health USING (host)\n              WHERE COALESCE(queue_counts.queued, 0) > 0\n                 OR COALESCE(queue_counts.active, 0) > 0\n                 OR COALESCE(queue_counts.succeeded, 0) > 0\n                 OR COALESCE(queue_counts.blocked, 0) > 0\n              ORDER BY active DESC, queued DESC, hosts.host\n              LIMIT 10")
    ).fetchall()
    hosts = []
    for row in host_rows:
        hosts.append(
            {
                "host": row["host"] or "(unknown host)",
                "queued": int(row["queued"]),
                "active": int(row["active"]),
                "succeeded": int(row["succeeded"]),
                "blocked": int(row["blocked"]),
                "policy": row["automation_status"] or "UNREVIEWED",
                "max_concurrency": row["max_concurrency"],
                "circuit_open": bool(row["circuit_open"]),
                "blocked_until": row["blocked_until_utc"],
                "http_status": row["last_http_status"],
                "consecutive_blocks": int(row["consecutive_blocks"] or 0),
            }
        )

    recent_rows = conn.execute(
        ALL_QUEUE_CTE
        + """SELECT queue.queue_kind, queue.download_job_id, queue.artifact_id,
                  queue.target_host,
                  queue.status, queue.last_result_status, queue.last_error,
                  queue.attempt_count, queue.updated_at_utc,
                  artifact.artifact_type, company.current_legal_name,
                  attempt.byte_size, attempt.detected_media_type
             FROM all_queue AS queue
             JOIN document_artifacts AS artifact
               ON artifact.artifact_id=queue.artifact_id
             JOIN companies AS company ON company.company_id=artifact.company_id
             LEFT JOIN artifact_download_attempts AS attempt
               ON attempt.download_attempt_id=(
                    SELECT latest.download_attempt_id
                     FROM artifact_download_attempts AS latest
                     WHERE latest.artifact_id=queue.artifact_id
                       AND latest.requested_url=queue.requested_url
                     ORDER BY latest.download_attempt_id DESC LIMIT 1
               )
            WHERE queue.status NOT IN ('PENDING', 'LEASED')
            ORDER BY queue.updated_at_utc DESC, queue.download_job_id DESC
            LIMIT 8"""
    ).fetchall()
    recent_items = [
        {
            "id": int(row["download_job_id"]),
            "queue_kind": row["queue_kind"],
            "artifact_id": int(row["artifact_id"]),
            "artifact_type": row["artifact_type"],
            "company": row["current_legal_name"],
            "host": row["target_host"] or "-",
            "status": row["status"],
            "result": row["last_result_status"] or "-",
            "error": _short_error(row["last_error"]),
            "attempt": int(row["attempt_count"]),
            "time_ago": _format_time_ago(row["updated_at_utc"]),
            "byte_size": int(row["byte_size"] or 0),
            "media_type": row["detected_media_type"] or "-",
        }
        for row in recent_rows
    ]

    transfer_row = conn.execute(
        """SELECT
             (SELECT COUNT(*)
                FROM artifact_download_attempts AS attempt
                JOIN document_artifacts AS artifact USING (artifact_id)
               WHERE artifact.artifact_type IN (
                   'credit_rating', 'presentation', 'transcript', 'recording'
               )) AS attempt_count,
             (SELECT COUNT(*)
                FROM artifact_contents AS content
                JOIN document_artifacts AS artifact USING (artifact_id)
               WHERE artifact.artifact_type IN (
                   'credit_rating', 'presentation', 'transcript', 'recording'
               )) AS validated_count,
             (SELECT COALESCE(SUM(content.byte_size), 0)
                FROM artifact_contents AS content
                JOIN document_artifacts AS artifact USING (artifact_id)
               WHERE artifact.artifact_type IN (
                   'credit_rating', 'presentation', 'transcript', 'recording'
               )) AS validated_bytes"""
    ).fetchone()

    type_counts = {
        str(row["artifact_type"]): int(row["count"])
        for row in conn.execute(
            ALL_QUEUE_CTE
            + """SELECT artifact.artifact_type, COUNT(*) AS count
                   FROM all_queue AS queue
                   JOIN document_artifacts AS artifact USING (artifact_id)
                  GROUP BY artifact.artifact_type
                  ORDER BY artifact.artifact_type"""
        )
    }

    from .browser_rating_monitor import fetch_rating_data
    return {
        'browser_ratings': fetch_rating_data(conn),
        "counts": counts,
        "total": total,
        "completed": completed,
        "pct": pct,
        "pending": pending,
        "leased": leased,
        "retryable": retryable,
        "succeeded": succeeded,
        "interaction_required": interaction_required,
        "failed": failed,
        "skipped": skipped,
        "ready": ready,
        "active_items": active_items,
        "hosts": hosts,
        "recent_items": recent_items,
        "attempt_count": int(transfer_row["attempt_count"] or 0),
        "validated_count": int(transfer_row["validated_count"] or 0),
        "validated_bytes": int(transfer_row["validated_bytes"] or 0),
        "type_counts": type_counts,
    }


def _status_badge(status: str) -> str:
    if status == "SUCCEEDED":
        return "[bold green]✅ DOWNLOADED[/bold green]"
    if status == "RETRYABLE_FAILURE":
        return "[bold yellow]↻ RETRY[/bold yellow]"
    if status == "INTERACTION_REQUIRED":
        return "[bold magenta]🔍 ACTION[/bold magenta]"
    if status.startswith("SKIPPED"):
        return "[dim blue]⏭ SKIPPED[/dim blue]"
    return "[bold red]❌ FAILED[/bold red]"


def render_rich_download_monitor(
    data: dict[str, Any], start_time: float, frame_idx: int, initial_completed: int
) -> Panel:
    """Build the Rich document-download monitor panel."""

    spinner = SPINNER_FRAMES[frame_idx % len(SPINNER_FRAMES)]
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    elapsed = max(0, int(time.time() - start_time))
    session_completed = max(0, data["completed"] - initial_completed)

    header = Table.grid(expand=True)
    header.add_column(justify="left")
    header.add_column(justify="center")
    header.add_column(justify="right")
    if data["leased"]:
        state = f"[bold white on green]  {spinner} DOWNLOADING ({data['leased']})  [/bold white on green]"
    elif data["ready"]:
        state = f"[bold black on yellow]  {data['ready']} READY  [/bold black on yellow]"
    elif data["pending"] or data["retryable"]:
        state = "[bold white on magenta]  WAITING / BACKOFF  [/bold white on magenta]"
    else:
        state = "[bold white on blue]  QUEUE IDLE  [/bold white on blue]"
    header.add_row(
        Text("TRADING-AI FILE ACQUISITION", style="bold cyan"),
        Text.from_markup(state),
        Text(now_utc, style="dim white"),
    )

    bar_len = 32
    filled = int(bar_len * data["pct"] / 100.0)
    progress = Table.grid(expand=True)
    progress.add_row(
        Text.from_markup(
            f" [bold cyan][{'█' * filled}{'░' * (bar_len - filled)}][/bold cyan] "
            f" [bold yellow]{data['pct']:5.1f}%[/bold yellow] "
            f"({data['completed']:,}/{data['total']:,} terminal)  "
            f"[dim]Session:[/dim] [bold green]+{session_completed}[/bold green]  "
            f"[dim]Elapsed:[/dim] {elapsed // 3600:02d}:{(elapsed % 3600) // 60:02d}:{elapsed % 60:02d}"
        )
    )
    type_summary = " | ".join(
        f"{artifact_type.replace('_', ' ').title()}: [bold]{count:,}[/bold]"
        for artifact_type, count in data["type_counts"].items()
    ) or "No queued artifact types"
    progress.add_row(Text.from_markup(f" [dim]Queue types:[/dim] {type_summary}"))
    progress.add_row(
        Text.from_markup(
            f" [dim]HTTP attempts:[/dim] [bold]{data['attempt_count']:,}[/bold] | "
            f"[dim]Validated files:[/dim] [bold green]{data['validated_count']:,}[/bold green] | "
            f"[dim]Stored bytes:[/dim] [bold cyan]{_format_bytes(data['validated_bytes'])}[/bold cyan] | "
            f"[dim]Claimable now:[/dim] [bold yellow]{data['ready']:,}[/bold yellow]"
        )
    )

    metrics = Table(box=None, expand=True, pad_edge=False)
    for label, style in (
        ("📥 Pending", "cyan"),
        ("⬇ Active", "magenta"),
        ("↻ Retry", "yellow"),
        ("✅ Downloaded", "green"),
        ("🔍 Action", "magenta"),
        ("❌ Failed", "red"),
        ("⏭ Skipped", "blue"),
    ):
        metrics.add_column(label, justify="center", style=f"bold {style}")
    metrics.add_row(
        f"{data['pending']:,}", f"{data['leased']:,}", f"{data['retryable']:,}",
        f"{data['succeeded']:,}", f"{data['interaction_required']:,}",
        f"{data['failed']:,}", f"{data['skipped']:,}",
    )

    active = Table(
        title=f"⬇ Active File Downloads ({len(data['active_items'])})",
        title_style="bold green", box=None, expand=True, header_style="bold dim",
    )
    active.add_column("Job", justify="right", width=6)
    active.add_column("Type", style="yellow")
    active.add_column("Company", style="bold white")
    active.add_column("Host", style="cyan")
    active.add_column("Worker", style="dim")
    active.add_column("Attempt", justify="right")
    active.add_column("Updated", style="yellow")
    if data["active_items"]:
        for item in data["active_items"]:
            active.add_row(
                str(item["id"]), item["artifact_type"], item["company"],
                item["host"], item["worker"], str(item["attempt"]),
                item["updated_ago"],
            )
    else:
        active.add_row(
            "-", "", "[dim]No files are being downloaded now[/dim]",
            "", "", "", "",
        )

    hosts = Table(
        title="🌐 Publisher Queues & Health", title_style="bold cyan", box=None,
        expand=True, header_style="bold dim",
    )
    hosts.add_column("Host", style="bold white")
    hosts.add_column("Queued", justify="right")
    hosts.add_column("Active", justify="right")
    hosts.add_column("Done", justify="right", style="green")
    hosts.add_column("Blocked", justify="right", style="red")
    hosts.add_column("Policy / Circuit")
    if data["hosts"]:
        for host in data["hosts"]:
            if host["circuit_open"]:
                health = (
                    f"[bold red]OPEN[/bold red] for "
                    f"{_format_time_until(host['blocked_until'])}"
                )
            elif host["policy"] == "BLOCKED_TERMS":
                health = "[bold red]BLOCKED_TERMS[/bold red]"
            elif host["policy"] == "NOT_DISALLOWED_ROBOTS":
                health = "[green]reviewed[/green]"
            else:
                health = f"[yellow]{host['policy']}[/yellow]"
            hosts.add_row(
                host["host"], str(host["queued"]), str(host["active"]),
                str(host["succeeded"]), str(host["blocked"]), health,
            )
    else:
        hosts.add_row("[dim]No publisher queues yet[/dim]", "", "", "", "", "")

    recent = Table(
        title="📜 Recent Download Outcomes", title_style="bold cyan", box=None,
        expand=True, header_style="bold dim",
    )
    recent.add_column("Time", width=11, style="dim")
    recent.add_column("Type", style="yellow")
    recent.add_column("Company", style="bold white")
    recent.add_column("Host", style="cyan")
    recent.add_column("Outcome", justify="center")
    recent.add_column("File", justify="right")
    recent.add_column("Result / Error", style="dim")
    if data["recent_items"]:
        for item in data["recent_items"]:
            detail = item["result"] if item["error"] == "-" else item["error"]
            recent.add_row(
                item["time_ago"], item["artifact_type"], item["company"],
                item["host"], _status_badge(item["status"]),
                _format_bytes(item["byte_size"]), detail,
            )
    else:
        recent.add_row(
            "-", "", "[dim]No completed download jobs yet[/dim]",
            "", "", "", "",
        )

    divider = Text("─" * 80, style="dim black")
    from .browser_rating_monitor import render_rating_panel
    return Panel(
        Group(header, divider, progress, divider, metrics, divider, active, divider, hosts, divider, recent,
              render_rating_panel(data.get('browser_ratings', {'hosts': [], 'workers': [], 'current': [], 'recent': []}))),
        title="[bold green]⬇ LIVE FILE DOWNLOAD MONITOR[/bold green]",
        subtitle="[dim]Press [bold white]Ctrl+C[/bold white] to exit | Auto-updating[/dim]",
        border_style="cyan", padding=(1, 2),
    )


def render_ansi_download_monitor(
    data: dict[str, Any], start_time: float, frame_idx: int, initial_completed: int
) -> str:
    """Render a dependency-free fallback for the download monitor."""

    spinner = SPINNER_FRAMES[frame_idx % len(SPINNER_FRAMES)]
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "\033[H\033[J",
        "=" * 88,
        f"  TRADING-AI FILE DOWNLOAD MONITOR  {spinner}  {now_utc}",
        "=" * 88,
        f"  Progress: {data['pct']:5.1f}% ({data['completed']:,}/{data['total']:,} terminal) | Ready: {data['ready']:,}",
        f"  Attempts: {data['attempt_count']:,} | Validated: {data['validated_count']:,} | Stored: {_format_bytes(data['validated_bytes'])}",
        "  Queue types: " + " | ".join(
            f"{artifact_type}={count:,}"
            for artifact_type, count in data["type_counts"].items()
        ),
        f"  Pending {data['pending']:,} | Active {data['leased']:,} | Retry {data['retryable']:,} | Downloaded {data['succeeded']:,} | Action {data['interaction_required']:,} | Failed {data['failed']:,} | Skipped {data['skipped']:,}",
        "-" * 88,
        "  ACTIVE DOWNLOADS:",
    ]
    if data["active_items"]:
        for item in data["active_items"]:
            lines.append(
                f"    #{item['id']} {item['company']} @ {item['host']} "
                f"[{item['artifact_type']}] attempt {item['attempt']} "
                f"({item['updated_ago']})"
            )
    else:
        lines.append("    (none)")
    lines.extend(("-" * 88, "  PUBLISHER QUEUES:"))
    for host in data["hosts"]:
        health = "CIRCUIT OPEN" if host["circuit_open"] else host["policy"]
        lines.append(
            f"    {host['host']:<32} queued={host['queued']:<5} active={host['active']:<3} "
            f"done={host['succeeded']:<5} {health}"
        )
    lines.extend(("-" * 88, "  RECENT OUTCOMES:"))
    for item in data["recent_items"][:6]:
        lines.append(
            f"    [{item['time_ago']:<10}] {item['company']:<24} "
            f"{item['artifact_type']:<14} {item['status']:<26} "
            f"{_format_bytes(item['byte_size'])}"
        )
    lines.extend(("=" * 88, "  Press Ctrl+C to exit monitor mode."))
    return "\n".join(lines)


def run_download_monitor(
    conn: sqlite3.Connection, refresh_interval: float = DEFAULT_REFRESH_INTERVAL
) -> None:
    """Continuously display download progress until interrupted."""

    if refresh_interval <= 0:
        raise ValueError("refresh_interval must be greater than zero")
    start_time = time.time()
    frame_idx = 0
    initial_data = fetch_download_monitor_data(conn)
    initial_completed = initial_data["completed"]

    if HAS_RICH:
        console = Console()
        console.clear()
        try:
            with Live(
                render_rich_download_monitor(
                    initial_data, start_time, frame_idx, initial_completed
                ),
                console=console, auto_refresh=False, screen=False,
            ) as live:
                while True:
                    time.sleep(refresh_interval)
                    frame_idx += 1
                    data = fetch_download_monitor_data(conn)
                    live.update(
                        render_rich_download_monitor(
                            data, start_time, frame_idx, initial_completed
                        ),
                        refresh=True,
                    )
        except KeyboardInterrupt:
            console.print("\n[bold yellow]Download monitor stopped by user.[/bold yellow]")
    else:
        try:
            while True:
                data = fetch_download_monitor_data(conn)
                sys.stdout.write(
                    render_ansi_download_monitor(
                        data, start_time, frame_idx, initial_completed
                    )
                )
                sys.stdout.flush()
                time.sleep(refresh_interval)
                frame_idx += 1
        except KeyboardInterrupt:
            print("\nDownload monitor stopped by user.")
