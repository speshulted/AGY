"""Bucket 2 (2-8w): Multi-Week Catalyst & Positioning Swings (Insider Clusters / Short-FTD)."""

from __future__ import annotations

from typing import Any

from src.config import UniverseConfig


def _build_swing_levels(direction: str, close: float, atr14: float | None, min_rr: float = 2.0) -> dict[str, float]:
    atr = atr14 if atr14 and atr14 > 0 else max(close * 0.035, 0.15)
    risk = max(2.0 * atr, close * 0.05)
    reward = risk * max(min_rr, 2.5)
    if direction == "LONG":
        stop = round(max(0.01, close - risk), 2)
        target = round(close + reward, 2)
        rr = round((target - close) / max(close - stop, 0.01), 2)
    else:
        stop = round(close + risk, 2)
        target = round(max(0.01, close - reward), 2)
        rr = round((close - target) / max(stop - close, 0.01), 2)
    return {
        "ref_price": round(close, 2),
        "entry_low": round(close * 0.985, 2),
        "entry_high": round(close * 1.015, 2),
        "stop_price": stop,
        "target_price": target,
        "reward_risk_ratio": rr,
    }


def score_bucket_2(record: dict[str, Any], cfg: UniverseConfig) -> list[dict[str, Any]]:
    """Score a ticker for Bucket 2 (2-8w) Long or Short swing setups."""
    market = record.get("market", {})
    sec = record.get("sec", {})
    short_info = record.get("short_interest", {})
    earnings = record.get("earnings", {})

    ticker = str(record["ticker"]).upper()
    close = market.get("close")
    ema20 = market.get("ema20")
    sma50 = market.get("sma50")
    mcap = market.get("market_cap") or 0.0
    is_micro_cap = cfg.min_market_cap <= mcap <= cfg.micro_cap_upper

    days_to_earnings = earnings.get("days_to_earnings")
    if days_to_earnings is None:
        days_to_earnings = market.get("days_to_earnings")
    eps_rev = str(earnings.get("eps_revision_direction", "UNKNOWN")).upper()
    is_pead = bool(earnings.get("is_pead", False))

    # Safeguard: require days_to_earnings >= 7 unless explicitly targeting PEAD
    if days_to_earnings is not None and 0 <= days_to_earnings < 7 and not is_pead:
        return []

    form4_txs = sec.get("form4_transactions", [])

    # Qualifying open-market non-10b5-1 insider buys by officers/directors
    buys = [
        tx for tx in form4_txs
        if tx.get("transaction_code") == "P"
        and not tx.get("is_10b5_1")
        and (tx.get("is_officer") or tx.get("is_director"))
    ]
    unique_buyers = {tx.get("insider_name") for tx in buys}
    total_buy_usd = sum(float(tx.get("value_usd", 0.0)) for tx in buys)
    insider_cluster_long = len(unique_buyers) >= 2 and total_buy_usd >= 100_000.0

    # Short squeeze setup: FINRA short-vol ratio > 50% + elevated FTDs + reclaim of 20d EMA + upward revisions
    short_ratio = short_info.get("latest_short_vol_ratio") or 0.0
    ftd_spike = bool(short_info.get("ftd_spike"))
    reclaim_ema20 = bool(ema20 is not None and close > ema20 and market.get("prev_close_below_ema20"))
    squeeze_long = short_ratio > 0.50 and ftd_spike and reclaim_ema20 and eps_rev in {"UP", "UNKNOWN"}

    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []
    if days_to_earnings is not None:
        warnings.append(f"Days to earnings: {days_to_earnings}")

    if insider_cluster_long or squeeze_long:
        score = 65.0
        reasons: list[str] = []
        if insider_cluster_long:
            score += min(20.0, 10.0 + len(unique_buyers) * 3.0)
            reasons.append(f"Insider cluster: {len(unique_buyers)} buyers totaling ${total_buy_usd:,.0f} (non-10b5-1)")
        if squeeze_long:
            score += 15.0
            reasons.append(f"Short/FTD squeeze setup: FINRA short-vol {short_ratio * 100:.1f}%, elevated FTDs, 20d EMA reclaim")

        levels = _build_swing_levels("LONG", close, market.get("atr14"), cfg.min_reward_risk_ratio)
        candidates.append(
            {
                "ticker": ticker,
                "bucket": 2,
                "horizon": "2-8w",
                "direction": "LONG",
                "score": round(score, 2),
                "market_cap": mcap,
                "rvol_20d": round(market.get("rvol_20d") or 0.0, 2),
                "is_micro_cap": is_micro_cap,
                "days_to_earnings": days_to_earnings,
                "reasons": reasons,
                "warnings": list(warnings),
                "sec_urls": sec.get("sec_urls", []),
                "metrics": market,
                "short_interest": short_info,
                **levels,
            }
        )

    # --- SHORT: Insider Distribution + Trend Roll ---
    officer_sales = [
        tx for tx in form4_txs
        if tx.get("transaction_code") == "S" and tx.get("is_officer")
    ]
    unique_sellers = {tx.get("insider_name") for tx in officer_sales}
    below_sma50_lh = bool(sma50 is not None and close < sma50 and market.get("has_lower_high_under_sma50"))
    rising_short = bool(short_info.get("rising_short_trend"))

    if len(unique_sellers) >= 3 and below_sma50_lh and rising_short and eps_rev in {"DOWN", "UNKNOWN"}:
        score = 68.0 + min(len(unique_sellers) * 3.0, 15.0)
        reasons = [
            f"Officer distribution: {len(unique_sellers)} officers selling",
            "Lower high below 50d SMA",
            f"Rising FINRA short-vol trend (5d {short_info.get('short_vol_ratio_5d')} > 20d {short_info.get('short_vol_ratio_20d')})",
        ]
        levels = _build_swing_levels("SHORT", close, market.get("atr14"), cfg.min_reward_risk_ratio)
        candidates.append(
            {
                "ticker": ticker,
                "bucket": 2,
                "horizon": "2-8w",
                "direction": "SHORT",
                "score": round(score, 2),
                "market_cap": mcap,
                "rvol_20d": round(market.get("rvol_20d") or 0.0, 2),
                "is_micro_cap": is_micro_cap,
                "days_to_earnings": days_to_earnings,
                "reasons": reasons,
                "warnings": list(warnings),
                "sec_urls": sec.get("sec_urls", []),
                "metrics": market,
                "short_interest": short_info,
                **levels,
            }
        )

    return candidates
