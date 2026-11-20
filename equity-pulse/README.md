# Equity Pulse — Daily Long/Short Equity Research Agent

Strictly **research-only** daily US equity screening, multi-agent filing synthesis, adversarial risk verification, and closed-loop SQLite signal ledger tracking.

> **Safety Boundary**: Never connect brokerage order-execution APIs. Blocked tickers (`GOOG`, `GOOGL`) are hard-enforced in `config/universe.yaml`, Stage 1 filters, and Stage 3 report validation.

## Quick Setup (Windows / PowerShell)

1. Ensure **Python 3.10+** (or `uv`) is installed:
   ```powershell
   winget install --id Python.Python.3.12 -e --source winget
   ```
2. Create a virtual environment and install dependencies:
   ```powershell
   cd equity-pulse
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and set your `SEC_USER_AGENT` (optional: `FINNHUB_API_KEY`, `FRED_API_KEY`, webhook/SMTP keys):
   ```powershell
   Copy-Item .env.example .env
   ```

## Verification & CLI Usage

- **Run hermetic unit tests**:
  ```powershell
  pytest
  ```
- **Run live end-to-end smoke test**:
  ```powershell
  python -m src.cli grade-ledger
  python -m src.cli ingest --smoke
  python -m src.cli screen --write-sample-report
  python -m src.cli validate-report --report reports/<YYYY-MM-DD>.md --candidates data/candidates_<YYYY-MM-DD>.json
  python -m src.cli notify --report reports/<YYYY-MM-DD>.md --dry-run
  ```

## Multi-Agent Workflow & Scheduling

- **Antigravity Workflow**: Run `workflows/daily_scan.py` via `run_workflow` to execute Stage 1 (deterministic CLI), Stage 2 (parallel `catalyst-swing-analyst` + `forensic-fundamental-analyst`), and Stage 3 (`risk-critic` + deterministic `validate-report` + ledger recording + mobile push).
- **Schedule (07:30 ET Weekdays)**:
  - Windows Task Scheduler: `.\scripts\install_schedule.ps1` (pass `-DryRun` to preview)
  - Unix / WSL cron: `./scripts/install_schedule.sh`
  - Antigravity Automations: Use `/automation` with cron `30 7 * * 1-5` (`America/New_York`).
