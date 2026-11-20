---
name: market-screener
description: >-
  Operational runbook and CLI schema for Equity Pulse Stage 1 quantitative screening,
  ledger grading, report validation, and mobile digest notification via `src/cli.py`.
  Use when running daily scans, grading signals, or validating daily reports.
---

# Market Screener CLI Runbook

Run all commands from the `equity-pulse/` directory using the project virtual environment (`.venv`).

## Commands

1. **Grade historical signals & generate 30-day Scorecard**:
   ```bash
   python -m src.cli grade-ledger --date YYYY-MM-DD
   ```
2. **Ingest OHLCV, SEC EDGAR, FINRA Reg SHO & optional macro/earnings feeds**:
   ```bash
   python -m src.cli ingest --date YYYY-MM-DD --smoke
   ```
   Pass `--tickers AAPL,NVDA,...` to override the ticker list.
3. **Run deterministic 3-bucket Stage 1 screen**:
   ```bash
   python -m src.cli screen --date YYYY-MM-DD
   ```
   Outputs `data/candidates_YYYY-MM-DD.json` containing:
   - `macro`: VIX, `iwm_below_sma50`, `choppy_regime`
   - `scorecard_30d`: Per-bucket Win %, Avg R-Multiple, Mean +5d return & +20d alpha vs `IWM`
   - `buckets`: `bucket_1` (1–5d), `bucket_2` (2–8w), `bucket_3` (1–6m+)
4. **Validate final report (`reports/YYYY-MM-DD.md`)**:
   ```bash
   python -m src.cli validate-report --report reports/YYYY-MM-DD.md --candidates data/candidates_YYYY-MM-DD.json
   ```
   Must exit `0`. Fails if any ticker is blocklisted, invented, lacks a counter-thesis, or violates `R:R >= 2.0`.
5. **Record verified survivors into SQLite Signal Ledger**:
   ```bash
   python -m src.cli record-ledger --report reports/YYYY-MM-DD.md --candidates data/candidates_YYYY-MM-DD.json --date YYYY-MM-DD
   ```
6. **Dispatch mobile digest**:
   ```bash
   python -m src.cli notify --report reports/YYYY-MM-DD.md
   ```
   Add `--dry-run` to preview without sending webhooks/email.
