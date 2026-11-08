"""Thin Screener CLI adapters; policy and services retain their existing owners."""
import json

COMMANDS = (
    'reprocess-screener',
    'screener-company-worker',
    'screener-discover-results',
    'screener-discovery-coverage',
    'screener-period-coverage',
    'screener-requeue-failed',
    'screener-requeue-incomplete-metrics',
)


def dispatch_screener(args, facade) -> int:
    if args.command == "screener-discover-results":
        try:
            payload = facade.discover_screener_results(
                query_role=args.query_role,
                query=args.query,
                fixture=args.fixture,
                dry_run=args.dry_run,
                db_path=args.db,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0 if payload["coverage_status"] == "complete" else 1

    if args.command == "screener-company-worker":
        try:
            payload = facade.run_screener_company_worker(
                due=args.due,
                limit=args.limit,
                dry_run=args.dry_run,
                db_path=args.db,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        failed = payload["summary"].get("outcome_counts", {})
        return 1 if any(key.startswith("failed_") and value for key, value in failed.items()) else 0

    if args.command == "screener-requeue-failed":
        try:
            payload = facade.requeue_failed_screener_companies(
                statuses=tuple(args.status or ("failed_terminal",)),
                paths=tuple(args.path or ()),
                limit=args.limit,
                dry_run=args.dry_run,
                db_path=args.db,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "screener-requeue-incomplete-metrics":
        try:
            payload = facade.requeue_incomplete_metric_screener_companies(
                paths=tuple(args.path or ()),
                limit=args.limit,
                dry_run=args.dry_run,
                db_path=args.db,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "screener-discovery-coverage":
        try:
            conn = facade.connect(args.db, read_only=True)
            try:
                facade.init_db(conn)
                output = facade.write_screener_discovery_coverage(conn, args.output)
                payload = json.loads(output.read_text(encoding="utf-8"))
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(
            json.dumps(
                {
                    "status": "ok",
                    "coverage_status": payload["coverage_status"],
                    "summary": payload["summary"],
                    "output": str(output.resolve()),
                },
                sort_keys=True,
            )
        )
        return 0 if payload["coverage_status"] == "complete" else 1

    if args.command == "screener-period-coverage":
        from ..commands.screener_period_coverage import run_period_coverage
        coverage_command = args.command == "screener-period-coverage"
        payload = run_period_coverage(
            period=args.period if coverage_command else args.result_period,
            symbols=args.symbol if coverage_command else (),
            repair=args.repair if coverage_command else not args.dry_run,
            refresh=args.refresh if coverage_command else not args.dry_run,
            limit=args.limit, db_path=args.db,
        )
        print(json.dumps(payload, sort_keys=True))
        return 1 if payload["missing_count"] or payload["refresh_failures"] else 0

    if args.command == "reprocess-screener":
        result = facade.reprocess_screener_blobs(
            args.symbol,
            args.scope,
            args.blob_id,
            args.peer_blob_id,
            db_path=args.db,
        )
    elif args.source == "screener":
        result = facade.scrape_screener_company(
            args.symbol,
            args.scope,
            db_path=args.db,
        )
    else:
        raise ValueError(f"Unsupported Screener command: {args.command}")
    print(
        json.dumps(
            {
                "status": "ok",
                "run_id": result.run_id,
                "fetch_id": result.fetch_id,
                "blob_id": result.blob_id,
                "parse_id": result.parse_id,
                "company_id": result.company_id,
                "reused_parse": result.reused_parse,
                "export_path": str(result.export_path.resolve()),
                **(
                    {
                        "requested_source_period": result.requested_source_period,
                        "resolved_source_period": result.resolved_source_period,
                    }
                    if args.command == "scrape-one" and args.source == "trendlyne"
                    else {}
                ),
            },
            sort_keys=True,
        )
    )
    return 0
