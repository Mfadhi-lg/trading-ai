#!/bin/bash
# Compatible CLI entry; operational shortcuts belong to the current dispatcher.
set -e
cd "$(dirname "$0")/../.."
case "${1:-}" in
    -h|--help)
        exec python3 -B ops/host/run_acquisition_lifecycle.py shell --help
        ;;
    dashboard)
        echo "The web control panel has been removed. Use ./scraper-monitor or ./scraper shell." >&2
        exit 2
        ;;
    downloads|rating|ratings)
        echo "This command has been removed. Use ./scraper monitor." >&2
        exit 2
        ;;
    status|coverage|bse|wake|shutdown|monitor|logs|doctor|search|company|shell)
        exec python3 -B ops/host/run_acquisition_lifecycle.py "$@"
        ;;
    start|resume|unpause)
        shift
        exec python3 -B ops/host/run_acquisition_lifecycle.py wake "$@"
        ;;
    stop|pause)
        shift
        exec python3 -B ops/host/run_acquisition_lifecycle.py shutdown "$@"
        ;;
    exchange-scheduler|run-acquisition-role|screener-company-worker|run-document-download-worker|quarter-processing-role)
        echo "Refused: continuous legacy acquisition is outside the current dispatcher lifecycle." >&2
        echo "See docs/operations/operational-recovery-20261022.md for explicit compatibility access." >&2
        exit 2
        ;;
esac
# Preserve the manual CLI and interactive shell. Legacy production writers
# require explicit operator access rather than lifecycle shortcuts.
if [ $# -eq 0 ]; then
    exec python3 -B ops/host/run_acquisition_lifecycle.py shell
else
    exec docker compose --profile manual run --rm cli "$@"
fi
