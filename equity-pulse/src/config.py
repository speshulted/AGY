"""Configuration loader for Equity Pulse."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional fallback if dotenv absent
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "universe.yaml"
DATA_DIR = PROJECT_ROOT / "data"
REPORTS_DIR = PROJECT_ROOT / "reports"
CACHE_DB_PATH = DATA_DIR / "market_cache.db"
LEDGER_DB_PATH = DATA_DIR / "signal_ledger.db"


@dataclass(frozen=True)
class UniverseConfig:
    allowed_exchanges: set[str]
    excluded_quote_types: set[str]
    do_not_trade: set[str]
    min_market_cap: float = 50_000_000.0
    min_avg_dollar_vol_20d: float = 1_000_000.0
    min_price: float = 2.0
    micro_cap_upper: float = 300_000_000.0
    min_short_float_shares: float = 10_000_000.0
    baby_shelf_float_cap: float = 75_000_000.0
    dilution_lookback_days: int = 90
    cooldown_days: dict[int, int] = field(default_factory=lambda: {1: 3, 2: 10, 3: 30})
    sec_filing_override_hours: int = 24
    min_reward_risk_ratio: float = 2.0
    max_candidates_per_bucket: int = 6
    numeric_tolerance_pct: float = 0.02
    long_slots_per_bucket: int = 3
    short_slots_per_bucket: int = 3
    value_min_fcf_yield_ev: float = 0.07
    growth_min_ttm_rev_growth: float = 0.15
    max_net_debt_ebitda: float = 2.5
    ohlcv_chunk_size: int = 150
    max_pass2_enrich: int = 120
    smoke_tickers: list[str] = field(default_factory=list)


def load_env(project_root: Path = PROJECT_ROOT) -> None:
    """Load .env from project root if present without overriding existing env vars."""
    env_file = project_root / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)


def load_universe_config(config_path: Path = CONFIG_PATH) -> UniverseConfig:
    """Parse config/universe.yaml into a typed UniverseConfig."""
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    liq = raw.get("liquidity", {})
    micro = raw.get("micro_cap", {})
    cd = raw.get("cooldown_trading_days", {})
    val = raw.get("validation", {})
    dual = raw.get("dual_strategy", {})
    fscan = raw.get("full_scan", {})

    return UniverseConfig(
        allowed_exchanges={str(x).upper() for x in raw.get("allowed_exchanges", ["NYSE", "NASDAQ", "NYSE AMERICAN"])},
        excluded_quote_types={str(x).upper() for x in raw.get("excluded_quote_types", ["ETF", "MUTUALFUND", "INDEX"])},
        do_not_trade={str(x).upper() for x in raw.get("do_not_trade", ["GOOG", "GOOGL"])},
        min_market_cap=float(liq.get("min_market_cap", 50_000_000)),
        min_avg_dollar_vol_20d=float(liq.get("min_avg_dollar_vol_20d", 1_000_000)),
        min_price=float(liq.get("min_price", 2.0)),
        micro_cap_upper=float(micro.get("upper_bound_market_cap", 300_000_000)),
        min_short_float_shares=float(micro.get("min_short_float_shares", 10_000_000)),
        baby_shelf_float_cap=float(micro.get("baby_shelf_float_cap", 75_000_000)),
        dilution_lookback_days=int(micro.get("dilution_lookback_days", 90)),
        cooldown_days={
            1: int(cd.get("bucket_1", 3)),
            2: int(cd.get("bucket_2", 10)),
            3: int(cd.get("bucket_3", 30)),
        },
        sec_filing_override_hours=int(cd.get("sec_filing_override_hours", 24)),
        min_reward_risk_ratio=float(val.get("min_reward_risk_ratio", 2.0)),
        max_candidates_per_bucket=int(val.get("max_candidates_per_bucket", 6)),
        numeric_tolerance_pct=float(val.get("numeric_tolerance_pct", 0.02)),
        long_slots_per_bucket=int(dual.get("long_slots_per_bucket", 3)),
        short_slots_per_bucket=int(dual.get("short_slots_per_bucket", 3)),
        value_min_fcf_yield_ev=float(dual.get("value_min_fcf_yield_ev", 0.07)),
        growth_min_ttm_rev_growth=float(dual.get("growth_min_ttm_rev_growth", 0.15)),
        max_net_debt_ebitda=float(dual.get("max_net_debt_ebitda", 2.5)),
        ohlcv_chunk_size=int(fscan.get("ohlcv_chunk_size", 150)),
        max_pass2_enrich=int(fscan.get("max_pass2_enrich", 120)),
        smoke_tickers=[str(t).upper() for t in raw.get("smoke_tickers", [])],
    )


def get_sec_user_agent() -> str:
    """Return SEC_USER_AGENT from environment with a clear fallback."""
    load_env()
    ua = os.getenv("SEC_USER_AGENT", "").strip()
    return ua or "EquityPulseResearch/0.1 (contact@example.com)"
