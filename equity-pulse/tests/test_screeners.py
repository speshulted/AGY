"""Hermetic tests for Universe filters, blocklist, cooldowns, and Buckets 1/2/3 scorers."""

import datetime as dt
from pathlib import Path

from src.config import load_universe_config
from src.ledger.store import SignalLedger
from src.screener.bucket_breakouts import score_bucket_1
from src.screener.bucket_fundamentals import score_bucket_3
from src.screener.bucket_swings import score_bucket_2
from src.screener.filters import passes_cooldown_filter, passes_universe_filter


def _base_record(ticker: str = "ACME", mcap: float = 150_000_000.0) -> dict:
    return {
        "ticker": ticker,
        "market": {
            "close": 25.0,
            "prev_close": 23.0,
            "gap_pct": 0.06,
            "sma20": 23.0,
            "ema20": 24.0,
            "sma50": 21.0,
            "sma200": 18.0,
            "high_20d": 24.5,
            "low_20d": 20.0,
            "high_55d": 24.8,
            "low_50d": 19.0,
            "low_52w": 21.0,
            "atr14": 1.2,
            "atr_extension": 1.6,
            "rvol_20d": 2.8,
            "avg_dollar_vol_20d": 5_000_000.0,
            "market_cap": mcap,
            "enterprise_value": mcap,
            "float_shares": 8_000_000.0,
            "exchange": "NASDAQ",
            "quote_type": "EQUITY",
            "prev_close_below_ema20": True,
            "stabilizing_rs": True,
        },
        "sec": {
            "has_sec_filings": True,
            "has_recent_24h_filing": False,
            "eight_k_events": [{"filing_date": "2026-11-19", "items": ["1.01"], "sentiment": "POSITIVE"}],
            "form4_transactions": [],
            "dilution": {"has_active_shelf_90d": True, "recent_forms": ["S-3"], "baby_shelf_constrained": True},
            "fundamentals": {},
            "sec_urls": ["https://www.sec.gov/example"],
        },
        "short_interest": {
            "latest_short_vol_ratio": 0.58,
            "short_vol_ratio_5d": 0.56,
            "short_vol_ratio_20d": 0.44,
            "rising_short_trend": True,
            "ftd_spike": True,
            "short_or_ftd_spike": True,
        },
        "earnings": {"days_to_earnings": 20, "eps_revision_direction": "UP"},
    }


def test_blocklist_and_liquidity_filters() -> None:
    cfg = load_universe_config()
    ok_goog, reason_goog = passes_universe_filter(_base_record("GOOGL", mcap=2_000_000_000_000), cfg)
    assert not ok_goog and reason_goog == "blocklisted_do_not_trade"

    ok_tiny, reason_tiny = passes_universe_filter(_base_record("TINY", mcap=20_000_000), cfg)
    assert not ok_tiny and reason_tiny == "below_min_market_cap"

    ok_good, _ = passes_universe_filter(_base_record("ACME", mcap=150_000_000), cfg)
    assert ok_good


def test_bucket_1_microcap_dilution_flag_and_squeeze_hard_reject() -> None:
    cfg = load_universe_config()
    # Long breakout on micro-cap with active S-3 gets dilution warning
    long_cands = score_bucket_1(_base_record("ACME", mcap=150_000_000), cfg)
    assert len(long_cands) == 1
    assert long_cands[0]["direction"] == "LONG"
    assert long_cands[0]["reward_risk_ratio"] >= 2.0
    assert any("DILUTION OVERHANG" in w for w in long_cands[0]["warnings"])

    # Short breakdown on micro-cap with float < 10M AND short/FTD spike MUST be hard-rejected
    short_rec = _base_record("TRAP", mcap=120_000_000)
    short_rec["market"]["close"] = 18.0
    short_rec["market"]["sma50"] = 22.0
    short_rec["market"]["low_20d"] = 19.5
    short_rec["market"]["float_shares"] = 5_000_000.0
    short_rec["sec"]["eight_k_events"] = [{"filing_date": "2026-11-19", "items": ["4.02"], "sentiment": "NEGATIVE"}]
    assert score_bucket_1(short_rec, cfg) == []


def test_bucket_2_insider_cluster_and_earnings_gate() -> None:
    cfg = load_universe_config()
    rec = _base_record("SWNG", mcap=600_000_000)
    rec["sec"]["form4_transactions"] = [
        {"insider_name": "CEO One", "is_officer": True, "is_director": True, "transaction_code": "P", "value_usd": 75_000, "is_10b5_1": False},
        {"insider_name": "CFO Two", "is_officer": True, "is_director": False, "transaction_code": "P", "value_usd": 50_000, "is_10b5_1": False},
    ]
    cands = score_bucket_2(rec, cfg)
    assert len(cands) == 1 and cands[0]["direction"] == "LONG"

    # Earnings in 3 days without PEAD must be skipped
    rec["earnings"]["days_to_earnings"] = 3
    assert score_bucket_2(rec, cfg) == []


def test_bucket_3_value_long_and_forensic_short() -> None:
    cfg = load_universe_config()
    val_rec = _base_record("VALU", mcap=800_000_000)
    val_rec["sec"]["dilution"]["has_active_shelf_90d"] = False
    val_rec["sec"]["fundamentals"] = {
        "ttm_revenue_growth": 0.14,
        "qoq_revenue_growth": 0.05,
        "free_cash_flow": 72_000_000,
        "operating_cash_flow": 85_000_000,
        "net_income": 60_000_000,
        "net_debt": 100_000_000,
        "ebitda": 110_000_000,
        "accrual_ratio": -0.02,
    }
    longs = score_bucket_3(val_rec, cfg)
    assert len(longs) == 1 and longs[0]["direction"] == "LONG"

    short_rec = _base_record("REDX", mcap=180_000_000)
    short_rec["sec"]["fundamentals"] = {
        "accrual_ratio": 0.12,
        "cash_runway_quarters": 2.5,
        "altman_z": 1.1,
    }
    shorts = score_bucket_3(short_rec, cfg)
    assert len(shorts) == 1 and shorts[0]["direction"] == "SHORT"
    assert any("BABY-SHELF" in w for w in shorts[0]["warnings"])

    # Quality Growth Investing Long (not near 52w low, but >=15% growth + positive FCF + OCF>NI + uptrend)
    growth_rec = _base_record("GRWT", mcap=2_500_000_000)
    growth_rec["market"]["close"] = 60.0
    growth_rec["market"]["low_52w"] = 30.0
    growth_rec["market"]["sma50"] = 54.0
    growth_rec["market"]["sma200"] = 48.0
    growth_rec["sec"]["dilution"]["has_active_shelf_90d"] = False
    growth_rec["sec"]["fundamentals"] = {
        "ttm_revenue_growth": 0.24,
        "qoq_revenue_growth": 0.08,
        "free_cash_flow": 90_000_000,
        "operating_cash_flow": 110_000_000,
        "net_income": 80_000_000,
        "net_debt": 50_000_000,
        "ebitda": 130_000_000,
        "accrual_ratio": -0.03,
    }
    growth_longs = score_bucket_3(growth_rec, cfg)
    assert len(growth_longs) == 1
    assert growth_longs[0]["direction"] == "LONG"
    assert growth_longs[0]["strategy_style"] == "Quality Growth Investing"


def test_ledger_cooldown_and_24h_sec_override(tmp_path: Path) -> None:
    cfg = load_universe_config()
    ledger = SignalLedger(tmp_path / "ledger.db")
    ledger.record_signal(
        {
            "run_date": "2026-11-18",
            "ticker": "ACME",
            "direction": "LONG",
            "bucket": 1,
            "ref_price": 25.0,
            "entry_low": 24.75,
            "entry_high": 25.25,
            "stop_price": 23.0,
            "target_price": 30.0,
            "reward_risk_ratio": 2.5,
        }
    )
    # 1 trading day later -> blocked by 3d cooldown
    ok_blocked, _ = passes_cooldown_filter("ACME", 1, dt.date(2026, 11, 19), False, cfg, ledger)
    assert not ok_blocked

    # Fresh 24h SEC filing overrides cooldown
    ok_override, _ = passes_cooldown_filter("ACME", 1, dt.date(2026, 11, 19), True, cfg, ledger)
    assert ok_override
