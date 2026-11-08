"""Thin document, rating and recovery CLI adapters; services retain ownership."""
import json
import sqlite3

COMMANDS = ('document-quality-report', 'reconcile-artifacts', 'backfill-result-artifacts', 'artifact-coverage', 'discover-documents', 'promote-document-links', 'build-document-recovery-targets', 'run-document-recovery-worker', 'document-alternate-source-recovery-report', 'reconcile-promoted-artifacts', 'queue-credit-rating-downloads', 'queue-document-downloads', 'queue-accepted-document-downloads', 'run-document-download-worker', 'document-recovery-report', 'requeue-document-downloads', 'register-document-replacement', 'import-manual-document', 'document-storage-projection', 'run-credit-rating-download-worker', 'fetch-documents', 'extract-document', 'normalize-credit-rating', 'ingest-official-transcript', 'normalize-recording', 'transcribe')

def dispatch_documents(args, facade) -> int:
    if args.command == "document-quality-report":
        try:
            conn = facade.connect(args.db, read_only=True)
            try:
                facade.init_db(conn)
                output = facade.write_document_quality_report(
                    conn,
                    args.output,
                    symbols=tuple(args.symbols or ()),
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
                    "coverage_status": payload["coverage_status"],
                    "pilot_status": payload["pilot"]["status"],
                    "summary": payload["summary"],
                    "output": str(output.resolve()),
                },
                sort_keys=True,
            )
        )
        return (
            0
            if payload["coverage_status"] == "complete"
            and payload["pilot"]["status"] == "complete"
            else 1
        )

    if args.command == "reconcile-artifacts":
        try:
            payload = facade.reconcile_artifacts(
                due=args.due,
                limit=args.limit,
                dry_run=args.dry_run,
                db_path=args.db,
                output=args.output,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "backfill-result-artifacts":
        try:
            payload = facade.backfill_accepted_result_artifacts(
                db_path=args.db, limit=args.limit
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "artifact-coverage":
        try:
            conn = facade.connect(args.db, read_only=True)
            try:
                facade.init_db(conn)
                output = facade.write_artifact_coverage_report(
                    conn, args.output, symbols=tuple(args.symbols or ())
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
                    "coverage_status": payload["coverage_status"],
                    "queue_status": payload["queue_status"],
                    "summary": payload["summary"],
                    "output": str(output.resolve()),
                },
                sort_keys=True,
            )
        )
        return 0

    if args.command == "discover-documents":
        try:
            payload = facade.discover_documents(
                symbol=args.symbol,
                manifest_path=args.manifest,
                dry_run=args.dry_run,
                db_path=args.db,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "promote-document-links":
        try:
            conn = facade.connect(args.db)
            try:
                facade.init_db(conn)
                payload = facade.promote_document_links(
                    conn,
                    document_type=args.document_type,
                    dry_run=args.dry_run,
                    limit=args.limit,
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "build-document-recovery-targets":
        try:
            conn = facade.connect(args.db)
            try:
                facade.init_db(conn)
                payload = facade.build_recovery_targets(
                    conn,
                    artifact_type=args.artifact_type,
                    dry_run=args.dry_run,
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "run-document-recovery-worker":
        try:
            conn = facade.connect(args.db)
            try:
                facade.init_db(conn)
                payload = facade.run_document_recovery_worker(
                    conn,
                    sources=args.sources,
                    limit=args.limit,
                    lease_seconds=args.lease_seconds,
                    throttle_seconds=args.throttle_seconds,
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "document-alternate-source-recovery-report":
        try:
            conn = facade.connect(args.db, read_only=True)
            try:
                facade.init_db(conn)
                payload, output = facade.write_alternate_source_recovery_report(
                    conn, output=args.output
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(
            json.dumps(
                {"status": "ok", **payload["summary"], "output": str(output.resolve())},
                sort_keys=True,
            )
        )
        return 0

    if args.command == "reconcile-promoted-artifacts":
        try:
            conn = facade.connect(args.db)
            try:
                facade.init_db(conn)
                payload = facade.reconcile_promoted_artifacts(
                    conn, limit=args.limit, dry_run=args.dry_run
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "queue-credit-rating-downloads":
        try:
            conn = facade.connect(args.db)
            try:
                facade.init_db(conn)
                payload = facade.queue_credit_rating_downloads(conn)
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "queue-document-downloads":
        try:
            payload = facade.queue_document_downloads(
                manifest_path=args.manifest,
                db_path=args.db,
                priority=args.priority,
                dry_run=args.dry_run,
            )
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "queue-accepted-document-downloads":
        try:
            conn = facade.connect(args.db)
            try:
                facade.init_db(conn)
                payload = facade.enqueue_documents_for_all_accepted_parses(conn)
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "run-document-download-worker":
        try:
            eligible_scopes = tuple(args.eligible_scopes)
            if eligible_scopes:
                from ..operations.workers.generic_scopes import validate_scopes
                validate_scopes(eligible_scopes)
            selected_scope_id = None if args.all_scopes else args.scope_id
            if args.max_bytes > facade.DOCUMENT_MAX_RESPONSE_BYTES:
                raise ValueError(
                    f"--max-bytes cannot exceed configured bound "
                    f"{facade.DOCUMENT_MAX_RESPONSE_BYTES}"
                )
            if args.timeout_seconds > facade.DOCUMENT_TOTAL_TIMEOUT_SECONDS:
                raise ValueError(
                    f"--timeout-seconds cannot exceed configured bound "
                    f"{facade.DOCUMENT_TOTAL_TIMEOUT_SECONDS}"
                )
            conn = facade.connect(args.db)
            try:
                facade.init_db(conn)
                from ..operations.workers.lifecycle import run_bounded
                payload = run_bounded(
                    facade.run_document_download_worker, conn,
                    **({"eligible_scopes": eligible_scopes} if eligible_scopes else {}),
                    scope_id=selected_scope_id,
                    artifact_types=tuple(args.artifact_types),
                    limit=args.limit,
                    lease_seconds=args.lease_seconds,
                    max_attempts=args.max_attempts,
                    throttle_seconds=args.throttle_seconds,
                    policy=facade.load_attribute('.documents.downloader', 'DownloadPolicy')(
                        max_bytes=args.max_bytes,
                        total_timeout_seconds=args.timeout_seconds,
                    ),
                )
                if eligible_scopes:
                    reports = {scope: facade.write_download_queue_report(conn, scope_id=scope)[0]
                               for scope in eligible_scopes}
                    report = {"bytes": {scope: item["bytes"] for scope, item in reports.items()}}
                    output = args.output or facade.load_attribute(".config", "DOCUMENT_DOWNLOAD_EXPORTS") / "queue" / "common-generic.json"
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(json.dumps(reports, sort_keys=True, indent=2) + "\n")
                else:
                    report, output = facade.write_download_queue_report(
                        conn, scope_id=selected_scope_id, output=args.output
                    )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(
            json.dumps(
                {
                    "status": "ok",
                    **payload,
                    "byte_report": report["bytes"],
                    "output": str(output.resolve()),
                },
                sort_keys=True,
            )
        )
        return 0 if not payload["outcome_counts"].get("WORKER_EXCEPTION") else 1

    if args.command == "document-recovery-report":
        try:
            conn = facade.connect(args.db, read_only=True)
            try:
                facade.init_db(conn)
                payload, output = facade.write_document_recovery_report(
                    conn,
                    scope_id=args.scope_id,
                    include_pending=args.include_pending,
                    output=args.output,
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(
            json.dumps(
                {"status": "ok", **payload["summary"], "output": str(output.resolve())},
                sort_keys=True,
            )
        )
        return 0

    if args.command == "requeue-document-downloads":
        try:
            conn = facade.connect(args.db)
            try:
                facade.init_db(conn)
                payload = facade.requeue_document_downloads(
                    conn,
                    queue_kind=args.queue_kind,
                    job_ids=args.job_ids,
                    reason_code=args.reason_code,
                    reason_detail=args.reason_detail,
                    dry_run=args.dry_run,
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "register-document-replacement":
        try:
            conn = facade.connect(args.db)
            try:
                facade.init_db(conn)
                payload = facade.register_replacement_document_url(
                    conn,
                    artifact_id=args.artifact_id,
                    replaces_artifact_source_id=args.replaces_source_id,
                    replacement_url=args.url,
                    reason_code=args.reason_code,
                    reason_detail=args.reason_detail,
                    evidence={"operator_note": args.evidence} if args.evidence else {},
                    dry_run=args.dry_run,
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "import-manual-document":
        try:
            conn = facade.connect(args.db)
            try:
                facade.init_db(conn)
                payload = facade.import_manual_document(
                    conn,
                    artifact_id=args.artifact_id,
                    artifact_source_id=args.source_id,
                    path=args.file,
                    official=args.official,
                    reason_detail=args.reason_detail,
                    evidence={"operator_note": args.evidence} if args.evidence else {},
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "document-storage-projection":
        try:
            planned: dict[str, int] = {}
            for value in args.planned_target:
                artifact_type, separator, count = value.partition("=")
                if not separator or not artifact_type or not count:
                    raise ValueError("--planned-target must use TYPE=COUNT")
                if artifact_type not in facade.load_attribute('.documents.downloader', 'ALLOWED_MEDIA_BY_ARTIFACT'):
                    raise ValueError(f"Unknown planned artifact type: {artifact_type}")
                planned[artifact_type] = int(count)
            conn = facade.connect(args.db, read_only=True)
            try:
                facade.init_db(conn)
                payload, output = facade.write_document_storage_projection(
                    conn,
                    scope_id=args.scope_id,
                    planned_target_counts=planned,
                    headroom_percent=args.headroom_percent,
                    document_store=args.document_store,
                    output=args.output,
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(
            json.dumps(
                {
                    "status": "ok",
                    "scope_id": args.scope_id,
                    **payload["actual"],
                    **payload["projection"],
                    "output": str(output.resolve()),
                },
                sort_keys=True,
            )
        )
        return 0

    if args.command == "run-credit-rating-download-worker":
        try:
            if args.max_bytes > facade.DOCUMENT_MAX_RESPONSE_BYTES:
                raise ValueError(
                    f"--max-bytes cannot exceed configured bound {facade.DOCUMENT_MAX_RESPONSE_BYTES}"
                )
            if args.timeout_seconds > facade.DOCUMENT_TOTAL_TIMEOUT_SECONDS:
                raise ValueError(
                    f"--timeout-seconds cannot exceed configured bound "
                    f"{facade.DOCUMENT_TOTAL_TIMEOUT_SECONDS}"
                )
            conn = facade.connect(args.db)
            try:
                facade.init_db(conn)
                from ..operations.workers.lifecycle import run_bounded
                executor = facade.run_credit_rating_download_worker
                selection = {"allowed_hosts": args.host}
                if args.rating_instance is not None:
                    from ..operations.workers.rating import RatingWorker
                    executor = RatingWorker(args.rating_instance).run
                    selection = {}
                payload = run_bounded(
                    executor, conn,
                    limit=args.limit,
                    policy=facade.load_attribute('.documents.downloader', 'DownloadPolicy')(
                        max_bytes=args.max_bytes,
                        total_timeout_seconds=args.timeout_seconds,
                    ),
                    **selection,
                    throttle_seconds=args.throttle_seconds,
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "fetch-documents":
        try:
            if args.max_bytes > facade.DOCUMENT_MAX_RESPONSE_BYTES:
                raise ValueError(
                    f"--max-bytes cannot exceed configured bound "
                    f"{facade.DOCUMENT_MAX_RESPONSE_BYTES}"
                )
            if args.timeout_seconds > facade.DOCUMENT_TOTAL_TIMEOUT_SECONDS:
                raise ValueError(
                    f"--timeout-seconds cannot exceed configured bound "
                    f"{facade.DOCUMENT_TOTAL_TIMEOUT_SECONDS}"
                )
            payload = facade.fetch_documents(
                symbol=args.symbol,
                artifact_types=tuple(args.artifact_types or ()),
                db_path=args.db,
                output=args.output,
                policy=facade.load_attribute('.documents.downloader', 'DownloadPolicy')(
                    max_bytes=args.max_bytes,
                    total_timeout_seconds=args.timeout_seconds,
                ),
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0 if payload["summary"]["rejected_url_count"] == 0 else 1

    if args.command == "extract-document":
        try:
            from ..documents.ocr import TesseractOcr

            payload = facade.extract_document(
                artifact_id=args.artifact_id,
                content_id=args.content_id,
                db_path=args.db,
                output=args.output,
                ocr_engine=TesseractOcr(language=args.ocr_language),
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0 if payload["extraction"]["extraction_status"] == "ok" else 1

    if args.command == "normalize-credit-rating":
        try:
            payload = facade.normalize_credit_rating_document(
                artifact_id=args.artifact_id,
                extraction_id=args.extraction_id,
                db_path=args.db,
                output=args.output,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return (
            0
            if payload["normalization"]["normalization_status"] == "accepted"
            else 1
        )

    if args.command == "ingest-official-transcript":
        try:
            payload = facade.ingest_official_transcript(
                artifact_id=args.artifact_id,
                extraction_id=args.extraction_id,
                db_path=args.db,
                output=args.output,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0 if payload["transcript"]["validation_status"] == "accepted" else 1

    if args.command == "normalize-recording":
        try:
            payload = facade.normalize_recording(
                artifact_id=args.artifact_id,
                content_id=args.content_id,
                db_path=args.db,
                output=args.output,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return (
            0
            if payload["normalization"]["normalization_status"] == "accepted"
            else 1
        )

    if args.command == "transcribe":
        try:
            from ..documents.transcription import FasterWhisperTranscriber

            payload = facade.transcribe_recording(
                artifact_id=args.artifact_id,
                recording_artifact_id=args.recording_artifact_id,
                comparison_requested=args.comparison_requested,
                transcriber=FasterWhisperTranscriber(
                    args.model_path,
                    device=args.device,
                    compute_type=args.compute_type,
                    language=args.language,
                ),
                db_path=args.db,
                output=args.output,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        transcription = payload["transcription"]
        if transcription is None:
            return 0
        return 0 if transcription["run_status"] == "accepted" else 1

    raise ValueError(f"Unsupported document command: {args.command}")
