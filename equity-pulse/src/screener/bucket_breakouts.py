"""Bucket 1 (1-5d): Short-Term Breakouts (Long) & Breakdowns (Short)."""

from __future__ import annotations

from typing import Any

from src.config import UniverseConfig


def _build_levels(direction: str, close: float, atr14: float | None, min_rr: float = 2.0) -> dict[str, float]:
    atr = atr14 if atr14 and atr14 > 0 else max(close * 0.03, 0.10)
    risk = max(1.25 * atr, close * 0.025)
    reward = risk * max(min_rr, 2.2)

    if direction == "LONG":
        entry_low = round(close * 0.99, 2)
        entry_high = round(close * 1.01, 2)
        stop = round(max(0.01, close - risk), 2)
        target = round(close + reward, 2)
        rr = round((target - close) / max(close - stop, 0.01), 2)
    else:
        entry_low = round(close * 0.99, 2)
        entry_high = round(close * 1.01, 2)
        stop = round(close + risk, 2)
        target = round(max(0.01, close - reward), 2)
        rr = round((close - target) / max(stop - close, 0.01), 2)

    return {
        "ref_price": round(close, 2),
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_price": stop,
        "target_price": target,
        "reward_risk_ratio": rr,
    }


def score_bucket_1(record: dict[str, Any], cfg: UniverseConfig) -> list[dict[str, Any]]:
    """Score a ticker for Bucket 1 (1-5d) Long Breakout or Short Breakdown."""
    market = record.get("market", {})
    sec = record.get("sec", {})
    short_info = record.get("short_interest", {})

    ticker = str(record["ticker"]).upper()
    close = market.get("close")
    sma50 = market.get("sma50")
    sma200 = market.get("sma200")
    high_20d = market.get("high_20d")
    high_55d = market.get("high_55d")
    low_20d = market.get("low_20d")
    low_50d = market.get("low_50d")
    rvol = market.get("rvol_20d") or 0.0
    gap_pct = market.get("gap_pct") or 0.0
    atr_ext = market.get("atr_extension")
    mcap = market.get("market_cap") or 0.0
    float_shares = market.get("float_shares")

    is_micro_cap = cfg.min_market_cap <= mcap <= cfg.micro_cap_upper
    dilution = sec.get("dilution", {})
    eight_ks = sec.get("eight_k_events", [])
    has_pos_8k = any(ev.get("sentiment") == "POSITIVE" for ev in eight_ks)
    has_neg_8k = any(ev.get("sentiment") == "NEGATIVE" for ev in eight_ks)

    candidates: list[dict[str, Any]] = []

    # --- LONG BREAKOUT ---
    breakout_high = bool(
        (high_20d is not None and close > high_20d)
        or (high_55d is not None and close > high_55d)
    )
    catalyst_gap = bool(gap_pct > 0.04 and has_pos_8k)
    trend_aligned = bool(sma50 is not None and sma200 is not None and close > sma50 > sma200)
    extension_ok = bool(atr_ext is None or atr_ext <= 2.5)

    if (breakout_high or catalyst_gap) and rvol >= 2.0 and trend_aligned and extension_ok:
        score = 60.0 + min((rvol - 2.0) * 10.0, 20.0)
        reasons = [f"RVOL20={rvol:.2f}x", "Close > SMA50 > SMA200"]
        warnings: list[str] = []

        if high_55d is not None and close > high_55d:
            score += 10.0
            reasons.append("55d breakout close")
        elif breakout_high:
            score += 6.0
            reasons.append("20d breakout close")
        if catalyst_gap:
            score += 12.0
            reasons.append(f"Gap {gap_pct * 100:.1f}% on positive 8-K")

        if is_micro_cap and dilution.get("has_active_shelf_90d"):
            score -= 20.0
            warnings.append(
                f"MICRO-CAP DILUTION OVERHANG: Active {', '.join(dilution.get('recent_forms', []))} shelf within 90d."
            )

        levels = _build_levels("LONG", close, market.get("atr14"), cfg.min_reward_risk_ratio)
        candidates.append(
            {
                "ticker": ticker,
                "bucket": 1,
                "horizon": "1-5d",
                "direction": "LONG",
                "score": round(score, 2),
                "market_cap": mcap,
                "rvol_20d": round(rvol, 2),
                "is_micro_cap": is_micro_cap,
                "reasons": reasons,
                "warnings": warnings,
                "sec_urls": sec.get("sec_urls", []),
                "metrics": market,
                **levels,
            }
        )

    # --- SHORT BREAKDOWN ---
    breakdown_low = bool(
        (low_20d is not None and close < low_20d)
        or (low_50d is not None and close < low_50d)
    )
    below_sma50 = bool(sma50 is not None and close < sma50)
    neg_catalyst = bool(has_neg_8k or market.get("failed_earnings_reaction"))

    if breakdown_low and rvol >= 2.0 and below_sma50 and neg_catalyst:
        # Micro-cap squeeze trap hard-reject: float < 10M AND short/FTD spike
        short_or_ftd_spike = bool(short_info.get("short_or_ftd_spike"))
        if is_micro_cap and float_shares is not None and float_shares < cfg.min_short_float_shares and short_or_ftd_spike:
            return candidates  # hard-reject micro-cap short squeeze trap

        score = 62.0 + min((rvol - 2.0) * 10.0, 20.0)
        reasons = [f"RVOL20={rvol:.2f}x", "Breakdown below 20d/50d support", "Close < SMA50"]
        warnings = []
        if has_neg_8k:
            score += 10.0
            reasons.append("Negative 8-K filing")
        if market.get("failed_earnings_reaction"):
            score += 8.0
            reasons.append("Failed earnings reaction")
        if float_shares is not None and float_shares < cfg.min_short_float_shares:
            warnings.append(f"Low float ({float_shares:,.0f} shares): monitor borrow & squeeze risk.")

        levels = _build_levels("SHORT", close, market.get("atr14"), cfg.min_reward_risk_ratio)
        candidates.append(
            {
                "ticker": ticker,
                "bucket": 1,
                "horizon": "1-5d",
                "direction": "SHORT",
                "score": round(score, 2),
                "market_cap": mcap,
                "rvol_20d": round(rvol, 2),
                "is_micro_cap": is_micro_cap,
                "reasons": reasons,
                "warnings": warnings,
                "sec_urls": sec.get("sec_urls", []),
                "metrics": market,
                "short_interest": short_info,
                **levels,
            }
        )

    return candidates
