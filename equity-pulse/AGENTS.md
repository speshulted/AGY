# Equity Pulse — Standing Agent Rules

See [.agents/AGENTS.md](./.agents/AGENTS.md) for the full rule set. Key invariants:
- Strictly research-only (no brokerage order-execution APIs).
- Zero hallucinated numbers: all quantitative figures in `reports/YYYY-MM-DD.md` must match `data/candidates_YYYY-MM-DD.json`.
- Enforce `config/universe.yaml` `do_not_trade` blocklist (`GOOG`, `GOOGL`).
- Require `R:R >= 2.0` and mandatory counter-thesis (`How this fails (Invalidation)` for Longs; `Squeeze / Upside Hazard` for Shorts).
