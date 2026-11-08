"""Interactive CLI shell (Claude-Code style) with real-time autocompletion and slash commands."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns

try:
    import prompt_toolkit
    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import HTML
    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False
    import readline

from ..config import DB_PATH, SCREENER_WORKER_LIMIT
from ..db.database import connect, init_db
from .monitor import run_live_monitor
from .download_monitor import run_download_monitor
from .render import (
    company_document_links,
    render_browse,
    render_company,
    render_coverage,
    render_mf,
    render_tables,
    resolve_company,
)
from ..pipeline.screener_discovery import (
    requeue_failed_screener_companies,
    requeue_incomplete_metric_screener_companies,
    run_screener_company_worker,
)

SLASH_COMMANDS = [
    ("/monitor", "Launch real-time live scraper pipeline process monitor dashboard"),
    ("/downloads", "Launch real-time file-download queue and publisher-health monitor"),
    ("/ratings", "Watch ICRA/CRISIL recovery: worker activity, saved files, waits and errors"),
    ("/search <query>", "Search companies by NSE symbol, BSE code, or company name"),
    ("/company <ticker>", "View detailed financial statements & period matrix for a company"),
    ("/coverage", "View Screener company coverage and current queue state table"),
    ("/mf", "View mutual fund stock-month features & holder flow analytics"),
    ("/tables", "List available database tables and views"),
    ("/browse <table>", "Browse raw database table rows (e.g. /browse companies)"),
    ("/worker [limit]", "Execute scraper company worker batch (default: 5)"),
    ("/requeue-failed", "Requeue all failed Screener company scraping items"),
    ("/requeue-inc", "Requeue Screener companies with incomplete metric observations"),
    ("/clear", "Clear the terminal screen"),
    ("/help", "Show this help banner with all available slash commands"),
    ("/exit", "Exit the interactive CLI shell session"),
]


class CompanyCompleter(Completer if HAS_PROMPT_TOOLKIT else object):
    """Autocompleter for company selection in interactive search/company dialogue prompts."""

    def __init__(self, company_cache: list[dict[str, Any]]) -> None:
        self.company_cache = company_cache

    def get_completions(self, document: Any, complete_event: Any):
        text = document.text_before_cursor.strip().lower()
        count = 0
        for c in self.company_cache:
            name = c["name"]
            nse = c["nse"]
            bse = c["bse"]

            if (
                not text
                or text in nse.lower()
                or text in bse.lower()
                or text in name.lower()
            ):
                symbol = nse if nse else (bse if bse else name)
                meta = f"{name} (BSE: {bse})" if bse else name
                yield Completion(
                    symbol,
                    start_position=-len(document.text_before_cursor),
                    display=f"{symbol:<14} {name}",
                    display_meta=meta,
                )
                count += 1
                if count >= 30:
                    break


def ensure_exchange_securities_seeded(conn: sqlite3.Connection) -> None:
    """Auto-seed exchange_securities table from exchange_securities_master.csv if table is empty."""
    try:
        count = conn.execute("SELECT COUNT(*) FROM exchange_securities").fetchone()[0]
        if count > 0:
            return

        root_dir = Path(__file__).resolve().parent.parent.parent
        csv_path = root_dir / "exchange_securities_master.csv"
        if not csv_path.exists():
            return

        import csv

        with open(csv_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            if not rows or not reader.fieldnames:
                return
            columns = reader.fieldnames
            placeholders = ", ".join(["?"] * len(columns))
            sql = f"INSERT OR REPLACE INTO exchange_securities ({', '.join(columns)}) VALUES ({placeholders})"

            data = []
            int_cols = {
                "exchange_security_id",
                "current_version_id",
                "is_present",
                "first_observed_snapshot_id",
                "last_observed_snapshot_id",
            }
            for r in rows:
                row_vals = []
                for col in columns:
                    v = r[col]
                    if v == "":
                        v = None
                    elif v is not None and v.isdigit() and col in int_cols:
                        v = int(v)
                    row_vals.append(v)
                data.append(row_vals)

            with conn:
                conn.executemany(sql, data)
    except Exception:
        pass


class CompanySlashCompleter(Completer if HAS_PROMPT_TOOLKIT else object):
    """Real-time as-you-type autocompleter for slash commands and company symbols/names."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.company_cache: list[dict[str, Any]] = []
        self._load_cache()

    def _load_cache(self) -> None:
        try:
            ensure_exchange_securities_seeded(self.conn)
            rows = self.conn.execute(
                """
                SELECT * FROM (
                    SELECT c.company_id, c.current_legal_name AS name,
                           (SELECT identifier_value FROM security_identifiers si
                             WHERE si.company_id=c.company_id AND si.identifier_type='NSE'
                               AND si.is_current=1 LIMIT 1) AS nse,
                           (SELECT identifier_value FROM security_identifiers si
                             WHERE si.company_id=c.company_id AND si.identifier_type='BSE'
                               AND si.is_current=1 LIMIT 1) AS bse
                    FROM companies c
                    UNION
                    SELECT 0 AS company_id, current_name_raw AS name,
                           COALESCE(nse_symbol, '') AS nse,
                           COALESCE(bse_scrip_code, '') AS bse
                    FROM exchange_securities
                    WHERE is_present=1
                )
                ORDER BY company_id DESC, name
                """
            ).fetchall()

            self.company_cache = []
            seen_nse = set()
            seen_bse = set()

            for r in rows:
                nse = r["nse"] or ""
                bse = r["bse"] or ""

                if nse and nse.lower() in seen_nse:
                    continue
                if bse and bse.lower() in seen_bse:
                    continue

                if nse:
                    seen_nse.add(nse.lower())
                if bse:
                    seen_bse.add(bse.lower())

                self.company_cache.append({
                    "id": r["company_id"] or 0,
                    "name": r["name"],
                    "nse": nse,
                    "bse": bse,
                })
        except Exception:
            self.company_cache = []

    def get_completions(self, document: Any, complete_event: Any):
        text = document.text_before_cursor
        text_strip = text.strip()

        # 1. Match slash commands at start of line
        if text.startswith("/"):
            if " " in text:
                parts = text.split(maxsplit=1)
                cmd = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ""

                resolved_cmd = resolve_slash_command(cmd)
                if resolved_cmd in ("/search", "/company"):
                    arg_lower = arg.lower()
                    count = 0
                    for c in self.company_cache:
                        name = c["name"]
                        nse = c["nse"]
                        bse = c["bse"]

                        if (
                            not arg_lower
                            or arg_lower in nse.lower()
                            or arg_lower in bse.lower()
                            or arg_lower in name.lower()
                        ):
                            symbol = nse if nse else (bse if bse else name)
                            meta = f"{name} (BSE: {bse})" if bse else name
                            yield Completion(
                                symbol,
                                start_position=-len(arg),
                                display=f"{symbol:<14} {name}",
                                display_meta=meta,
                            )
                            count += 1
                            if count >= 30:
                                break
                    return
            else:
                typed_cmd = text.lower()
                for slash_cmd, desc in SLASH_COMMANDS:
                    cmd_name = slash_cmd.split()[0]
                    if cmd_name.startswith(typed_cmd):
                        yield Completion(
                            cmd_name,
                            start_position=-len(text),
                            display=f"{cmd_name:<16} {desc}",
                            display_meta=desc,
                        )
                return

        # 2. If user types plain text (e.g. "TAT" or "RELIANCE"), suggest companies
        if text_strip:
            text_lower = text_strip.lower()
            count = 0
            for c in self.company_cache:
                name = c["name"]
                nse = c["nse"]
                bse = c["bse"]

                if (
                    text_lower in nse.lower()
                    or text_lower in bse.lower()
                    or text_lower in name.lower()
                ):
                    symbol = nse if nse else (bse if bse else name)
                    meta = f"{name} (BSE: {bse})" if bse else name
                    yield Completion(
                        symbol,
                        start_position=-len(text_strip),
                        display=f"{symbol:<14} {name}",
                        display_meta=meta,
                    )
                    count += 1
                    if count >= 30:
                        break


CANONICAL_COMMANDS = {
    "/monitor",
    "/downloads",
    "/search",
    "/company",
    "/coverage",
    "/mf",
    "/tables",
    "/browse",
    "/worker",
    "/requeue-failed",
    "/requeue-inc",
    "/clear",
    "/help",
    "/exit",
}

COMMAND_ALIASES = {
    "/s": "/search",
    "/c": "/company",
    "/q": "/exit",
    "/quit": "/exit",
    "/h": "/help",
    "/?": "/help",
    "/cls": "/clear",
    "/mon": "/monitor",
    "/moniter": "/monitor",
    "/download-monitor": "/downloads",
    "/dl": "/downloads",
    "/comp": "/company",
    "/compa": "/company",
    "/sea": "/search",
    "/sear": "/search",
    "/cov": "/coverage",
    "/cover": "/coverage",
    "/tab": "/tables",
    "/brow": "/browse",
    "/work": "/worker",
    "/screener-worker": "/worker",
    "/requeue-fail": "/requeue-failed",
    "/requeue-incomplete": "/requeue-inc",
}


def resolve_slash_command(cmd_input: str) -> str:
    """Resolve aliases, case-insensitivity, and unique prefixes for slash commands."""
    cmd_lower = cmd_input.strip().lower()
    if cmd_lower in COMMAND_ALIASES:
        return COMMAND_ALIASES[cmd_lower]
    if cmd_lower in CANONICAL_COMMANDS:
        return cmd_lower

    if cmd_lower.startswith("/"):
        matches = [c for c in CANONICAL_COMMANDS if c.startswith(cmd_lower)]
        if len(matches) == 1:
            return matches[0]

    return cmd_lower


def prompt_interactive_company_search(
    console: Console,
    completer: CompanySlashCompleter,
    action_label: str = "search",
    prompt_title: str = "Search Company",
) -> str | None:
    """Interactive popup dialogue box for searching and selecting companies as-you-type."""
    console.print(
        f"\n[bold cyan]🔍 {prompt_title}[/bold cyan] [dim](as-you-type dropdown enabled. Type name/ticker & press Enter, or empty Enter to cancel)[/dim]"
    )
    if HAS_PROMPT_TOOLKIT:
        sub_session = PromptSession(
            completer=CompanyCompleter(completer.company_cache),
            complete_while_typing=True,
            auto_suggest=AutoSuggestFromHistory(),
        )
        try:
            chosen = sub_session.prompt(
                HTML(f'<cyan><b>trading-ai</b></cyan> <yellow><b>[{action_label}] &gt;</b></yellow> ')
            ).strip()
            return chosen if chosen else None
        except (KeyboardInterrupt, EOFError):
            return None
    else:
        try:
            chosen = input(f"\033[1;36mtrading-ai\033[0m \033[1;33m[{action_label}] >\033[0m ").strip()
            return chosen if chosen else None
        except (KeyboardInterrupt, EOFError):
            return None


def search_companies(conn: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    """Search companies by symbol, BSE code, or legal name fragment across DB & exchange master."""
    q = query.strip()
    if not q:
        return []

    ensure_exchange_securities_seeded(conn)
    like_param = f"%{q.lower()}%"
    rows = conn.execute(
        """
        SELECT * FROM (
            SELECT c.company_id, c.current_legal_name AS name,
                   (SELECT identifier_value FROM security_identifiers si
                     WHERE si.company_id=c.company_id AND si.identifier_type='NSE'
                       AND si.is_current=1 LIMIT 1) AS nse,
                   (SELECT identifier_value FROM security_identifiers si
                     WHERE si.company_id=c.company_id AND si.identifier_type='BSE'
                       AND si.is_current=1 LIMIT 1) AS bse,
                   cs.latest_discovered_result_period AS discovered,
                   cs.latest_accepted_result_period AS accepted
            FROM companies c
            LEFT JOIN screener_company_collection_state cs
              ON cs.company_id = c.company_id AND cs.reporting_scope='consolidated'
            WHERE LOWER(c.current_legal_name) LIKE ?
               OR c.company_id IN (
                   SELECT company_id FROM security_identifiers
                   WHERE LOWER(identifier_value) LIKE ? AND is_current = 1
               )
            UNION
            SELECT 0 AS company_id, current_name_raw AS name,
                   COALESCE(nse_symbol, '-') AS nse,
                   COALESCE(bse_scrip_code, '-') AS bse,
                   '-' AS discovered,
                   '-' AS accepted
            FROM exchange_securities
            WHERE is_present=1
              AND (LOWER(current_name_raw) LIKE ?
                   OR LOWER(COALESCE(nse_symbol, '')) LIKE ?
                   OR LOWER(COALESCE(bse_scrip_code, '')) LIKE ?)
        )
        ORDER BY
           CASE
               WHEN LOWER(nse) = LOWER(?) THEN 0
               WHEN LOWER(name) = LOWER(?) THEN 1
               WHEN LOWER(name) LIKE ? THEN 2
               ELSE 3
           END,
           company_id DESC,
           name
        LIMIT 50
        """,
        (like_param, like_param, like_param, like_param, like_param, q, q, f"{q.lower()}%"),
    ).fetchall()

    deduped = []
    seen_nse = set()
    seen_bse = set()

    for r in rows:
        nse = (r["nse"] or "-").lower()
        bse = (r["bse"] or "-").lower()

        if nse != "-" and nse in seen_nse:
            continue
        if bse != "-" and bse in seen_bse:
            continue

        if nse != "-":
            seen_nse.add(nse)
        if bse != "-":
            seen_bse.add(bse)

        deduped.append({
            "id": r["company_id"],
            "name": r["name"],
            "nse": r["nse"] or "-",
            "bse": r["bse"] or "-",
            "discovered": r["discovered"] or "-",
            "accepted": r["accepted"] or "-",
        })
        if len(deduped) >= 25:
            break

    return deduped


def get_company_metrics_summary(conn: sqlite3.Connection, company_id: int) -> dict[str, Any]:
    """Fetch latest financial metrics overview for a company."""
    parse_row = conn.execute(
        """
        SELECT parse_id, accepted_at_utc
        FROM latest_accepted_pages
        WHERE company_id=? AND source='screener' AND reporting_scope='consolidated'
        ORDER BY accepted_at_utc DESC LIMIT 1
        """,
        (company_id,),
    ).fetchone()

    if not parse_row:
        return {"status": "No financial statements parsed yet"}

    parse_id = parse_row["parse_id"]

    sales = conn.execute(
        """
        SELECT period_label_raw, display_value
        FROM financial_observations
        WHERE parse_id=? AND section_code='profit_loss'
          AND LOWER(line_item_raw) LIKE '%sales%'
        ORDER BY column_position DESC LIMIT 1
        """,
        (parse_id,),
    ).fetchone()

    profit = conn.execute(
        """
        SELECT period_label_raw, display_value
        FROM financial_observations
        WHERE parse_id=? AND section_code='profit_loss'
          AND LOWER(line_item_raw) LIKE '%net profit%'
        ORDER BY column_position DESC LIMIT 1
        """,
        (parse_id,),
    ).fetchone()

    return {
        "parse_id": parse_id,
        "accepted_at": parse_row["accepted_at_utc"],
        "latest_period": sales["period_label_raw"] if sales else "-",
        "latest_sales": sales["display_value"] if sales else "-",
        "latest_profit": profit["display_value"] if profit else "-",
    }


def render_search_view(console: Console, conn: sqlite3.Connection, query: str) -> None:
    """Render rich search results view or detailed company card."""
    results = search_companies(conn, query)

    if not results:
        console.print(f"\n[bold red]❌ No companies found matching '{query}'[/bold red]\n")
        return

    if len(results) == 1 or results[0]["nse"].upper() == query.strip().upper():
        comp = results[0]
        metrics = get_company_metrics_summary(conn, comp["id"])

        table = Table.grid(expand=True, padding=(0, 2))
        table.add_column(style="bold cyan", width=20)
        table.add_column(style="white")

        table.add_row("Company Name:", f"[bold white]{comp['name']}[/bold white]")
        table.add_row("NSE Symbol:", f"[bold yellow]{comp['nse']}[/bold yellow]")
        table.add_row("BSE Code:", comp["bse"])
        table.add_row("Internal ID:", str(comp["id"]))
        table.add_row("Latest Discovered:", f"[cyan]{comp['discovered']}[/cyan]")
        table.add_row("Latest Accepted:", f"[green]{comp['accepted']}[/green]")

        if "latest_period" in metrics:
            table.add_row("Latest Period:", metrics["latest_period"])
            table.add_row("Sales/Revenue:", f"[bold green]{metrics['latest_sales']}[/bold green]")
            table.add_row("Net Profit:", f"[bold green]{metrics['latest_profit']}[/bold green]")

        documents = company_document_links(conn, int(comp["id"]), per_type=1)
        for index, document in enumerate(documents):
            link = Text()
            link.append(
                f"{document['type_label']} · {document['period']}\n",
                style="bold white",
            )
            link.append(f"{document['title']}\n", style="dim")
            url = str(document["url"])
            link.append(url, style=f"underline cyan link {url}")
            table.add_row("Documents:" if index == 0 else "", link)

        panel = Panel(
            table,
            title=f"[bold green]🏢 COMPANY SEARCH RESULT: {comp['nse']} [/bold green]",
            subtitle=f"[dim]Run [bold white]/company {comp['nse']}[/bold white] to view full period financial statements[/dim]",
            border_style="cyan",
            padding=(1, 2),
        )
        console.print(panel)
        return

    table = Table(
        title=f"🔍 Search Results for '{query}' ({len(results)} matches)",
        title_style="bold cyan",
        border_style="dim",
        header_style="bold cyan",
        expand=True,
    )
    table.add_column("ID", justify="right", style="dim", width=6)
    table.add_column("Company Legal Name", style="bold white")
    table.add_column("NSE Ticker", style="bold yellow")
    table.add_column("BSE Code", style="dim")
    table.add_column("Discovered", style="cyan")
    table.add_column("Accepted", style="green")

    for r in results:
        table.add_row(
            str(r["id"]),
            r["name"],
            r["nse"],
            r["bse"],
            r["discovered"],
            r["accepted"],
        )

    console.print(table)
    console.print(
        f"[dim]Tip: Run [bold white]/company <NSE>[/bold white] to view complete statements.[/dim]\n"
    )


def print_welcome_banner(console: Console) -> None:
    """Print Claude-Code style banner on launch."""
    console.clear()
    banner_text = Text()
    banner_text.append("🤖 TRADING-AI INTERACTIVE CLI & REAL-TIME SEARCH\n", style="bold green")
    banner_text.append("─────────────────────────────────────────────────────────────\n", style="dim black")
    banner_text.append(" • ", style="cyan")
    banner_text.append("/monitor", style="bold yellow")
    banner_text.append("          - Real-time live scraper dashboard\n", style="dim white")
    banner_text.append(" • ", style="cyan")
    banner_text.append("/downloads", style="bold yellow")
    banner_text.append("        - Real-time file download dashboard\n", style="dim white")
    banner_text.append(" • ", style="cyan")
    banner_text.append("/search <query>", style="bold yellow")
    banner_text.append("  - Real-time search by ticker or name\n", style="dim white")
    banner_text.append(" • ", style="cyan")
    banner_text.append("/company <ticker>", style="bold yellow")
    banner_text.append(" - View company statement matrix\n", style="dim white")
    banner_text.append(" • ", style="cyan")
    banner_text.append("/coverage", style="bold yellow")
    banner_text.append("        - Screener pipeline coverage table\n", style="dim white")
    banner_text.append(" • ", style="cyan")
    banner_text.append("/help", style="bold yellow")
    banner_text.append("            - Show all available slash commands\n", style="dim white")
    banner_text.append(" • ", style="cyan")
    banner_text.append("/exit", style="bold yellow")
    banner_text.append("            - Quit interactive session\n", style="dim white")

    panel = Panel(
        banner_text,
        title="[bold cyan]⚡ Welcome to Trading-AI Interactive CLI Shell[/bold cyan]",
        subtitle="[dim]Type a slash command or company name (as-you-type suggestions active)[/dim]",
        border_style="cyan",
        padding=(1, 2),
    )
    console.print(panel)


def print_help_banner(console: Console) -> None:
    """Print complete slash commands guide."""
    table = Table(
        title="📋 Available Slash Commands",
        title_style="bold green",
        header_style="bold cyan",
        border_style="dim",
        expand=True,
    )
    table.add_column("Command", style="bold yellow", width=24)
    table.add_column("Description", style="white")

    for cmd, desc in SLASH_COMMANDS:
        table.add_row(cmd, desc)

    console.print(table)


def run_interactive_shell(db_path: Path = DB_PATH) -> int:
    """Main interactive REPL shell execution loop with real-time autocompletion."""
    console = Console()
    conn = connect(db_path)
    try:
        init_db(conn)
    except Exception as exc:
        console.print(f"[bold red]Database connection error: {exc}[/bold red]")

    completer = CompanySlashCompleter(conn)

    if HAS_PROMPT_TOOLKIT:
        session = PromptSession(
            completer=completer,
            complete_while_typing=True,
            auto_suggest=AutoSuggestFromHistory(),
        )
    else:
        # Fallback readline completer
        def _readline_completer(text: str, state: int) -> str | None:
            cmd_names = [c[0].split()[0] for c in SLASH_COMMANDS]
            matches = [c for c in cmd_names if c.startswith(text)]
            if state < len(matches):
                return matches[state]
            return None

        readline.set_completer(_readline_completer)
        readline.parse_and_bind("tab: complete")

    print_welcome_banner(console)

    while True:
        try:
            if HAS_PROMPT_TOOLKIT:
                user_input = session.prompt(
                    HTML('<cyan><b>trading-ai</b></cyan> <yellow><b>&gt;</b></yellow> ')
                ).strip()
            else:
                user_input = input("\033[1;36mtrading-ai\033[0m \033[1;33m>\033[0m ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[bold yellow]Exiting interactive CLI shell. Goodbye! 👋[/bold yellow]")
            break

        if not user_input:
            continue

        parts = user_input.split(maxsplit=1)
        raw_cmd = parts[0].lower()
        args_str = parts[1].strip() if len(parts) > 1 else ""

        if not raw_cmd.startswith("/"):
            # If user typed a ticker directly e.g. "RELIANCE", treat as search
            cmd = "/search"
            args_str = user_input
        else:
            cmd = resolve_slash_command(raw_cmd)

        if cmd in ("/exit", "/quit", "/q"):
            console.print("[bold yellow]Exiting interactive CLI shell. Goodbye! 👋[/bold yellow]")
            break

        elif cmd == "/clear":
            console.clear()
            print_welcome_banner(console)

        elif cmd == "/help":
            print_help_banner(console)

        elif cmd == "/monitor":
            console.print(
                "[bold cyan]Launching real-time live scraper monitor... (Press Ctrl+C to exit back to shell)[/bold cyan]"
            )
            time.sleep(0.5)
            try:
                run_live_monitor(conn)
            except Exception as exc:
                console.print(f"[bold red]Monitor error: {exc}[/bold red]")
            console.clear()
            print_welcome_banner(console)

        elif cmd == "/ratings":
            from .browser_rating_monitor import run_rating_monitor
            run_rating_monitor(conn)

        elif cmd == "/downloads":
            console.print(
                "[bold cyan]Launching live file download monitor... "
                "(Press Ctrl+C to exit back to shell)[/bold cyan]"
            )
            time.sleep(0.5)
            try:
                run_download_monitor(conn)
            except Exception as exc:
                console.print(f"[bold red]Download monitor error: {exc}[/bold red]")
            console.clear()
            print_welcome_banner(console)

        elif cmd == "/search":
            if not args_str:
                query = prompt_interactive_company_search(
                    console, completer, action_label="search", prompt_title="Search Company"
                )
                if query:
                    render_search_view(console, conn, query)
            else:
                render_search_view(console, conn, args_str)

        elif cmd == "/company":
            if not args_str:
                ticker = prompt_interactive_company_search(
                    console, completer, action_label="company", prompt_title="View Company Statements"
                )
                if ticker:
                    try:
                        output = render_company(conn, ticker)
                        console.print(output)
                    except Exception as exc:
                        console.print(f"[bold red]Error rendering company: {exc}[/bold red]")
            else:
                try:
                    output = render_company(conn, args_str)
                    console.print(output)
                except Exception as exc:
                    console.print(f"[bold red]Error rendering company: {exc}[/bold red]")

        elif cmd == "/coverage":
            try:
                output = render_coverage(conn)
                console.print(output)
            except Exception as exc:
                console.print(f"[bold red]Error rendering coverage: {exc}[/bold red]")

        elif cmd == "/mf":
            try:
                output = render_mf(conn, limit=25)
                console.print(output)
            except Exception as exc:
                console.print(f"[bold red]Error rendering mutual funds: {exc}[/bold red]")

        elif cmd == "/tables":
            try:
                output = render_tables(conn)
                console.print(output)
            except Exception as exc:
                console.print(f"[bold red]Error listing tables: {exc}[/bold red]")

        elif cmd == "/browse":
            if not args_str:
                console.print(render_tables(conn))
                console.print("[bold yellow]Usage: /browse <table_name> [--where <condition>][/bold yellow]\n")
            else:
                try:
                    table_name = args_str.split()[0]
                    where_cond = (
                        args_str[len(table_name):].strip()
                        if len(args_str) > len(table_name)
                        else None
                    )
                    output = render_browse(conn, table_name, where=where_cond, limit=25)
                    console.print(output)
                except Exception as exc:
                    console.print(f"[bold red]Error browsing table: {exc}[/bold red]")

        elif cmd == "/worker":
            limit = int(args_str) if args_str.isdigit() else SCREENER_WORKER_LIMIT
            console.print(f"[bold green]Running scraper company worker batch (limit={limit})...[/bold green]")
            try:
                result = run_screener_company_worker(conn, limit=limit)
                console.print(f"[bold cyan]Worker result: {result}[/bold cyan]")
            except Exception as exc:
                console.print(f"[bold red]Worker error: {exc}[/bold red]")

        elif cmd == "/requeue-failed":
            console.print("[bold green]Requeueing failed Screener companies...[/bold green]")
            try:
                n = requeue_failed_screener_companies(conn)
                console.print(f"[bold cyan]Requeued {n} failed queue items.[/bold cyan]")
            except Exception as exc:
                console.print(f"[bold red]Requeue error: {exc}[/bold red]")

        elif cmd == "/requeue-inc":
            console.print(
                "[bold green]Requeueing Screener companies with incomplete metric observations...[/bold green]"
            )
            try:
                n = requeue_incomplete_metric_screener_companies(conn)
                console.print(f"[bold cyan]Requeued {n} incomplete queue items.[/bold cyan]")
            except Exception as exc:
                console.print(f"[bold red]Requeue error: {exc}[/bold red]")

        else:
            console.print(
                f"[bold red]Unknown slash command '{raw_cmd}'. Type /help for available commands.[/bold red]"
            )

    conn.close()
    return 0
