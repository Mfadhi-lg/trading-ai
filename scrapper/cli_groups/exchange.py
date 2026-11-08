"""Thin exchange CLI adapters; services and policy remain at existing paths.

The compatibility facade supplies patchable dependencies at dispatch time.
Importing this module acquires no database, network or worker resources.
"""
from contextlib import closing
import json
import sqlite3

COMMANDS = (
    'exchange-bulk-enqueue',
    'exchange-discover-results',
    'exchange-import-reference',
    'exchange-inspect-dataset',
    'exchange-reconciliation-report',
    'exchange-refresh-universe',
    'exchange-resolve-conflict',
    'exchange-scheduler',
    'exchange-unaccepted-backfill',
    'exchange-universe-coverage',
)


def dispatch_exchange(args, facade) -> int:
    if args.command == "exchange-import-reference":
        from ..sources.exchange.reference import locate_reference_file, read_reference_rows
        from ..workflows.exchange.reference_import import import_exchange_reference
        conn = None
        try:
            path = args.file if args.file is not None else locate_reference_file()
            read_reference_rows(path)  # Reject malformed input before opening a writer.
            # Require an existing compatible DB; importing never initializes schema.
            with closing(facade.connect(args.db, read_only=True)) as reader:
                facade.init_db(reader)
            conn = facade.connect(args.db)
            result = import_exchange_reference(conn, path)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        finally:
            if conn is not None:
                conn.close()
        print(json.dumps(result, sort_keys=True))
        return 0

    if args.command in {"exchange-refresh-universe", "exchange-discover-results"}:
        runner = (
            facade.refresh_exchange_universe
            if args.command == "exchange-refresh-universe"
            else facade.discover_exchange_results
        )
        try:
            runner_options = dict(
                dataset=args.dataset,
                file=args.file,
                url=args.url,
                effective_date=args.effective_date,
                dry_run=args.dry_run,
                db_path=args.db,
                override_validation=args.override_validation,
            )
            if args.command == "exchange-discover-results":
                runner_options["result_period"] = args.result_period
            payload = runner(**runner_options)
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        # A rejected snapshot is stored but changes nothing, and a filing that
        # cannot be resolved to a company is a discovery gap, not a technical
        # failure. Both must be visible in the exit status.
        if (payload.get("validation") or {}).get("status") == "failed":
            return 1
        review = (payload.get("enqueue") or {}).get("manual_review", 0)
        return 1 if review else 0

    if args.command == "exchange-inspect-dataset":
        try:
            payload = facade.inspect_exchange_dataset(
                dataset=args.dataset,
                file=args.file,
                url=args.url,
                result_period=args.result_period,
                sample_rows=args.sample_rows,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, indent=2, sort_keys=True))
        return 0

    if args.command == "exchange-reconciliation-report":
        try:
            conn = facade.connect(args.db, read_only=True)
            try:
                facade.init_db(conn)
                output = facade.write_exchange_reconciliation_report(
                    conn, args.output, args.snapshot_id
                )
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
                    "reconciliation_status": payload["reconciliation_status"],
                    "summary": payload["summary"],
                    "output": str(output.resolve()),
                },
                sort_keys=True,
            )
        )
        # An open identity conflict blocks automation: this exit code is the gate
        # the scheduler is only turned on behind.
        return 1 if payload["summary"]["open_conflict_count"] else 0

    if args.command == "exchange-scheduler":
        try:
            if args.resume_access:
                if args.status or args.weekly_report or args.catch_up or args.once or args.attempt:
                    raise ValueError("--resume-access cannot be combined with scheduler runs")
                # Preserve the existing missing-hook NameError, including its
                # facade patch path, until separately selected behavior work.
                try:
                    resume_source_access = facade.resume_source_access
                except AttributeError:
                    raise NameError("name 'resume_source_access' is not defined") from None
                payload = resume_source_access(
                    args.resume_access, args.resume_evidence or "", db_path=args.db
                )
            elif args.resume_evidence:
                raise ValueError("--resume-evidence requires --resume-access")
            elif args.status:
                payload = facade.day_status(args.scheduled_for, db_path=args.db)
            elif args.weekly_report:
                payload = facade.run_weekly_report(db_path=args.db)
            elif args.catch_up:
                payload = facade.catch_up_missed_days(
                    db_path=args.db, max_days=args.catch_up_days
                )
            else:
                payload = facade.run_scheduler(
                    once=args.once, attempt=args.attempt, db_path=args.db
                )
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 1 if payload.get("status") not in (None, "ok") else 0

    if args.command == "exchange-resolve-conflict":
        try:
            conn = facade.connect(args.db)
            try:
                facade.init_db(conn)
                payload = facade.resolve_identity_conflict(
                    conn,
                    conflict_id=args.conflict_id,
                    resolution=args.resolution,
                    now_utc=facade.utc_now(),
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "exchange-bulk-enqueue":
        from scrapper.commands.exchange_bulk_enqueue import run_bulk_enqueue
        try:
            conn = facade.connect(args.db)
            try:
                facade.init_db(conn)
                payload = run_bulk_enqueue(conn, dry_run=args.dry_run)
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "exchange-unaccepted-backfill" and args.result_period:
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

    if args.command == "exchange-unaccepted-backfill":
        from scrapper.commands.exchange_unaccepted_backfill import run_unaccepted_backfill
        try:
            conn = facade.connect(args.db)
            try:
                facade.init_db(conn)
                payload = run_unaccepted_backfill(conn, dry_run=args.dry_run)
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "exchange-universe-coverage":
        try:
            conn = facade.connect(args.db, read_only=True)
            try:
                facade.init_db(conn)
                output = facade.write_exchange_universe_coverage(conn, args.output)
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
                    "missing_required_datasets": payload["missing_required_datasets"],
                    "summary": payload["summary"],
                    "output": str(output.resolve()),
                },
                sort_keys=True,
            )
        )
        return 0 if payload["coverage_status"] == "complete" else 1

    raise ValueError(f"Unsupported exchange command: {args.command}")
