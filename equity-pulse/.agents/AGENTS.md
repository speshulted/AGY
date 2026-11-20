# Equity Pulse — Standing Agent Rules

1. **Strictly Research-Only**: Never integrate brokerage execution APIs, order routing, or automated trading clients.
2. **Zero Invented Numbers**: Every price, market cap, RVOL, cash balance, short-volume ratio, accrual ratio, and valuation multiple cited in `reports/YYYY-MM-DD.md` MUST come verbatim (within rounding tolerance) from `data/candidates_YYYY-MM-DD.json`. Use web search and SEC filings only for qualitative context (contract terms, litigation, management commentary, FDA/catalyst dates).
3. **Hard Blocklist Enforcement**: Never analyze, recommend, or output a setup card for any ticker in `config/universe.yaml` `do_not_trade` (default: `GOOG`, `GOOGL`).
4. **Minimum Reward-to-Risk (`R:R >= 2.0`)**: Every setup card in `reports/YYYY-MM-DD.md` MUST include `Ref Price`, `Entry Zone`, `Stop Price`, and `Target Price` with mathematical `R:R >= 2.0` aligned to direction (`LONG`: `stop < entry < target`; `SHORT`: `target < entry < stop`).
5. **Mandatory Counter-Thesis**:
   - Every **LONG** card MUST contain a `How this fails (Invalidation)` bullet.
   - Every **SHORT** card MUST contain a `Squeeze / Upside Hazard` bullet citing float size, FINRA short-vol %, and upcoming binary catalysts.
6. **Deterministic Gate**: Always run `python -m src.cli validate-report --report reports/YYYY-MM-DD.md --candidates data/candidates_YYYY-MM-DD.json` and confirm exit code `0` before recording to the ledger or dispatching notifications.
