"""Thin mutual-fund and Trendlyne CLI adapters; services retain ownership."""
import json

COMMANDS = ('derive-mf-features', 'export-mf-features', 'mf-coverage-report', 'crosscheck-mfdata', 'import-mf', 'import-mf-directory', 'trendlyne-pilot-plan', 'trendlyne-pilot-backfill')

def dispatch_mutual_funds(args, facade) -> int:
    if args.command == "derive-mf-features":
        conn = facade.connect(args.db)
        try:
            facade.init_db(conn)
            results = facade.derive_stock_month_features_through(
                conn,
                publisher=args.publisher,
                through_month=args.through_month,
                generated_at_utc=facade.utc_now(),
            )
        finally:
            conn.close()
        print(json.dumps({
            "status": "ok",
            "publisher": args.publisher,
            "through_month": args.through_month,
            "feature_sets": [item.to_dict() for item in results],
        }, sort_keys=True))
        return 0

    if args.command == "export-mf-features":
        publisher_path = args.publisher or "all_publishers"
        output = args.output or (
            facade.MF_FEATURE_EXPORTS / publisher_path / args.scope
            / f"through-{args.through_month}.json"
        )
        conn = facade.connect(args.db, read_only=True)
        try:
            facade.init_db(conn)
            written = facade.write_mf_feature_export(
                conn,
                scope=args.scope,
                through_month=args.through_month,
                publisher=args.publisher,
                output=output,
            )
        finally:
            conn.close()
        print(json.dumps({
            "status": "ok", "output": str(written.resolve()),
            "scope": args.scope, "through_month": args.through_month,
        }, sort_keys=True))
        return 0

    if args.command == "mf-coverage-report":
        output = args.output or (
            facade.MF_COVERAGE_EXPORTS / args.publisher
            / f"through-{args.through_month}.json"
        )
        conn = facade.connect(args.db, read_only=True)
        try:
            facade.init_db(conn)
            written = facade.write_mf_coverage_report(
                conn,
                publisher=args.publisher,
                through_month=args.through_month,
                month_count=args.months,
                output=output,
            )
            payload = json.loads(written.read_text(encoding="utf-8"))
        finally:
            conn.close()
        print(json.dumps({
            "status": "ok",
            "coverage_status": payload["coverage_status"],
            "output": str(written.resolve()),
            "summary": payload["summary"],
        }, sort_keys=True))
        return 0 if payload["coverage_status"] == "complete" else 1

    if args.command == "crosscheck-mfdata":
        try:
            payload = facade.execute_mfdata_crosscheck(
                publisher=args.publisher,
                scheme_code=args.scheme_code,
                family_id=args.family_id,
                disclosure_month=args.month,
                path=args.path,
                db_path=args.db,
                output=args.output,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command in {"import-mf", "import-mf-directory"}:
        try:
            if args.command == "import-mf":
                proposals = facade.plan_import(
                    publisher=args.publisher,
                    disclosure_month=args.month,
                    path=args.path,
                    db_path=args.db,
                )
            else:
                proposals = facade.plan_directory_import(path=args.path, db_path=args.db)
            payload = (
                facade.import_plan_payload(proposals, dry_run=True)
                if args.dry_run
                else facade.execute_import(
                    proposals, db_path=args.db, export_root=facade.MF_WORKBOOK_EXPORTS
                )
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 1 if any(
            item["proposed_action"] == "quarantine"
            or (
                not args.dry_run
                and item.get("import_state") in {"rejected", "quarantine"}
            )
            for item in payload["files"]
        ) else 0

    if args.command == "trendlyne-pilot-plan":
        conn = facade.connect(args.db, read_only=not args.persist)
        try:
            facade.init_db(conn)
            plan = facade.build_trendlyne_pilot_plan(
                conn,
                end_source_period=args.end_period,
                month_count=args.months,
                batch_size=args.batch_size,
                symbols=tuple(args.symbols or facade.load_attribute('.pipeline.trendlyne_pilot', 'PILOT_SYMBOLS')),
            )
        finally:
            conn.close()
        print(json.dumps({"event": "request_plan", **plan.to_dict()}, sort_keys=True))
        if args.persist:
            plan_id, _ = facade.register_trendlyne_pilot_plan(
                end_source_period=args.end_period,
                month_count=args.months,
                batch_size=args.batch_size,
                symbols=tuple(args.symbols or facade.load_attribute('.pipeline.trendlyne_pilot', 'PILOT_SYMBOLS')),
                db_path=args.db,
            )
            print(json.dumps({"event": "plan_registered", "plan_id": plan_id}))
        return 0

    if args.command == "trendlyne-pilot-backfill":
        result = facade.run_trendlyne_pilot_batch(
            end_source_period=args.end_period,
            month_count=args.months,
            batch_size=args.batch_size,
            symbols=tuple(args.symbols or facade.load_attribute('.pipeline.trendlyne_pilot', 'PILOT_SYMBOLS')),
            db_path=args.db,
            on_plan=lambda plan: print(
                json.dumps({"event": "request_plan", **plan.to_dict()}, sort_keys=True),
                flush=True,
            ),
        )
        print(json.dumps({"event": "batch_result", **result.to_dict()}, sort_keys=True))
        return 1 if result.failed_request_count else 0

    result = facade.scrape_trendlyne_company(
        args.symbol,
        requested_source_period=args.period,
        db_path=args.db,
    )

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
