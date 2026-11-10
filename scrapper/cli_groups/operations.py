"""Thin operations, view and quality-report adapters; owners remain unchanged."""
import json

COMMANDS = ('shell', 'init-db', 'monitor', 'download-monitor', 'monitor-downloads', 'view', 'quality-report')

def dispatch_operations(args, facade) -> int:
    if args.command is None or args.command == "shell":
        db_path = getattr(args, "db", facade.DB_PATH)
        return facade.run_interactive_shell(db_path)

    if args.command == "init-db":
        from ..db.database import selected_backend
        if selected_backend() == 'postgresql':
            import os
            from ..db.postgresql import connect, PostgreSQLSettings
            from ..db.postgresql_schema import migrate
            schema = os.environ.get('TRADING_AI_POSTGRESQL_SCHEMA', '')
            with connect(PostgreSQLSettings.from_env()) as conn:
                migrate(conn, schema)
            print(json.dumps({'status': 'ok', 'backend': 'postgresql', 'schema': schema,
                              'runtime_setup': 'explicit identity and least-privilege role provisioning required'}))
            return 0
        conn = facade.connect(args.db)
        try:
            facade.init_db(conn, apply_migrations=True)
        finally:
            conn.close()
        print(json.dumps({"status": "ok", "database": str(args.db.resolve())}))
        return 0

    if args.command == "monitor":
        conn = facade.connect(args.db, read_only=True)
        try:
            facade.init_db(conn)
            facade.run_live_monitor(conn, refresh_interval=args.refresh)
        finally:
            conn.close()
        return 0

    if args.command in {"download-monitor", "monitor-downloads"}:
        conn = facade.connect(args.db, read_only=True)
        try:
            facade.init_db(conn)
            facade.run_download_monitor(conn, refresh_interval=args.refresh)
        finally:
            conn.close()
        return 0

    if args.command == "view":
        try:
            conn = facade.connect(args.db, read_only=True)
            try:
                facade.init_db(conn)
                if args.view_mode == "company":
                    sections = (
                        [s.strip() for s in args.sections.split(",") if s.strip()]
                        if args.sections
                        else None
                    )
                    unknown = set(sections or ()) - set(facade.load_attribute('.view.render', 'SECTION_CODES'))
                    if unknown:
                        raise ValueError(
                            f"Unknown section(s): {', '.join(sorted(unknown))}. "
                            f"Choose from: {', '.join(facade.load_attribute('.view.render', 'SECTION_CODES'))}"
                        )
                    output = facade.render_company(
                        conn,
                        args.term,
                        scope=args.scope,
                        periods=args.periods,
                        sections=sections,
                        show_shareholding=not args.no_shareholding,
                    )
                elif args.view_mode == "mf":
                    output = facade.render_mf(
                        conn,
                        publisher=args.publisher,
                        month=args.month,
                        scope=args.scope,
                        isin=args.isin,
                        limit=args.limit,
                    )
                elif args.view_mode == "coverage":
                    output = facade.render_coverage(conn)
                elif args.view_mode == "tables":
                    output = facade.render_tables(conn)
                elif args.view_mode == "monitor":
                    facade.run_live_monitor(conn, refresh_interval=args.refresh)
                    return 0
                elif args.view_mode == "downloads":
                    facade.run_download_monitor(conn, refresh_interval=args.refresh)
                    return 0
                else:
                    output = facade.render_browse(
                        conn, args.table, where=args.where, limit=args.limit
                    )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(output)
        return 0

    if args.command == "quality-report":
        conn = facade.connect(args.db, read_only=True)
        try:
            facade.init_db(conn)
            output = facade.write_quality_report(conn, args.output)
        finally:
            conn.close()
        print(json.dumps({"status": "ok", "output": str(output.resolve())}))
        return 0
