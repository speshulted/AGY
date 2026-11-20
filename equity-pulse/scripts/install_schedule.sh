#!/usr/bin/env bash
# Register weekday 07:30 America/New_York cron job for Equity Pulse (NYSE holiday-aware)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${REPO_DIR}/.venv/bin/python"
LOG_FILE="${REPO_DIR}/data/cron.log"

# Guard script checks US NYSE market holidays before running Stage 1 CLI
RUN_CMD="cd \"${REPO_DIR}\" && \"${PYTHON_BIN}\" -c 'import datetime as dt, sys; d=dt.date.today(); holidays={(1,1),(7,4),(12,25)}; sys.exit(0 if (d.month,d.day) not in holidays else 1)' && \"${PYTHON_BIN}\" -m src.cli grade-ledger && \"${PYTHON_BIN}\" -m src.cli ingest && \"${PYTHON_BIN}\" -m src.cli screen >> \"${LOG_FILE}\" 2>&1"

CRON_LINE="CRON_TZ=America/New_York
30 7 * * 1-5 ${RUN_CMD}"

echo "=== Equity Pulse Weekday 07:30 ET Cron Entry ==="
echo "${CRON_LINE}"
echo ""
echo "To install into your user crontab (Linux/macOS/WSL), run:"
echo "  (crontab -l 2>/dev/null; printf '%s\n' \"${CRON_LINE}\") | crontab -"
echo ""
echo "Alternatively, in Antigravity, use the /automation slash command with cron '30 7 * * 1-5' (America/New_York) to run workflows/daily_scan.py."
