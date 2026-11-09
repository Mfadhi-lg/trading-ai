"""Operator CLI: native schema setup, verified SQLite backup and atomic import."""
import argparse
import json
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="Read SQLite and create a new verified snapshot")
    export.add_argument("--source", type=Path, required=True)
    export.add_argument("--snapshot", type=Path, required=True)
    export.add_argument("--batch-size", type=int, default=1000)
    for name in ("schema", "import"):
        command = sub.add_parser(name)
        command.add_argument("--schema", required=True)
        command.add_argument("--allow-destination-write", action="store_true", required=True)
        if name == "import":
            command.add_argument("--snapshot", type=Path, required=True)
            command.add_argument("--snapshot-sha256", required=True)
            command.add_argument("--batch-size", type=int, default=1000)
    for command in sub.choices.values():
        command.add_argument("--report", type=Path, required=True, help="New private report file; never overwritten")
    args = parser.parse_args(argv)
    # Reserve the report first. Never overwrite an existing report/source file.
    with args.report.open("x", encoding="utf-8") as handle:
        args.report.chmod(0o600)
        report = {"status": "running", "command": args.command}
        json.dump(report, handle)
        handle.flush()
        def progress(event):
            report["last_progress"] = event
            print(json.dumps(event, sort_keys=True), file=sys.stderr, flush=True)
        try:
            if args.command == "export":
                from .postgresql_transfer import export_snapshot
                result = export_snapshot(args.source, args.snapshot, batch_size=args.batch_size, progress=progress)
            else:
                from .postgresql import PostgreSQLSettings, connect
                settings = PostgreSQLSettings.from_env()
                if args.command == "schema":
                    from .postgresql_schema import migrate
                    with connect(settings) as conn:
                        migrate(conn, args.schema)
                    result = {"status": "committed", "schema": args.schema}
                else:
                    from .postgresql_transfer import transfer
                    result = transfer(settings, args.snapshot, args.schema, expected_sha256=args.snapshot_sha256, batch_size=args.batch_size, progress=progress)
            report.update(result)
            code = 0
        except Exception as error:
            # Server diagnostics may include credentials, SQL parameters or raw
            # source records. Reports contain phase/type, never exception text.
            report.update(status="failed", error_type=type(error).__name__)
            print("Migration failed; inspect the report's phase and error type. No raw server diagnostics are printed.", file=sys.stderr)
            code = 1
        handle.seek(0)
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.truncate()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
