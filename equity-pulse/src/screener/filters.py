"""Universe gate, blocklist enforcement, and ledger cooldown deduplication."""

from __future__ import annotations

import datetime as dt
from typing import Any

from src.config import UniverseConfig
from src.ledger.store import SignalLedger


def passes_universe_filter(
    record: dict[str, Any],
    cfg: UniverseConfig,
) -> tuple[bool, str]:
    """Apply Section 3.1 deterministic pre-filters before bucket scoring."""
    ticker = str(record.get("ticker", "")).upper()
    if not ticker:
        return False, "missing_ticker"

    # 1. Blocklist check (highest priority)
    if ticker in cfg.do_not_trade:
        return False, "blocklisted_do_not_trade"

    market = record.get("market", {})
    sec = record.get("sec", {})

    # 2. Exchange & security type check
    quote_type = str(market.get("quote_type", "EQUITY")).upper()
    if quote_type in cfg.excluded_quote_types:
        return False, f"excluded_quote_type:{quote_type}"

    exchange = str(market.get("exchange", "")).upper()
    if exchange and exchange not in cfg.allowed_exchanges:
        return False, f"disallowed_exchange:{exchange}"

    if sec.get("has_sec_filings") is False:
        return False, "no_sec_filings"

    # 3. Liquidity & price floors
    price = market.get("close")
    mcap = market.get("market_cap")
    addv = market.get("avg_dollar_vol_20d")

    if price is None or float(price) < cfg.min_price:
        return False, "below_min_price"
    if mcap is None or float(mcap) < cfg.min_market_cap:
        return False, "below_min_market_cap"
    if addv is None or float(addv) < cfg.min_avg_dollar_vol_20d:
        return False, "below_min_addv"

    return True, "ok"


def passes_cooldown_filter(
    ticker: str,
    bucket: int,
    run_date: dt.date,
    has_recent_24h_sec_filing: bool,
    cfg: UniverseConfig,
    ledger: SignalLedger | None,
) -> tuple[bool, str]:
    """Enforce per-bucket cooldowns (3d / 10d / 30d) unless a new 8-K/Form 4 arrived in 24h."""
    if ledger is None:
        return True, "no_ledger"

    cooldown_days = cfg.cooldown_days.get(bucket, 5)
    on_cooldown = ledger.is_on_cooldown(
        ticker=ticker,
        bucket=bucket,
        as_of=run_date,
        cooldown_days=cooldown_days,
        has_recent_sec_filing=has_recent_24h_sec_filing,
    )
    if on_cooldown:
        return False, f"ledger_cooldown_bucket_{bucket}"
    return True, "ok"
