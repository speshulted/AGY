"""Bucket 3 (1-6m+): Fundamental Value Longs & Forensic Red-Flag Shorts."""

from __future__ import annotations

from typing import Any

from src.config import UniverseConfig


def _build_fundamental_levels(direction: str, close: float, min_rr: float = 2.0) -> dict[str, float]:
    risk = close * 0.12
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
        "entry_low": round(close * 0.97, 2),
        "entry_high": round(close * 1.03, 2),
        "stop_price": stop,
        "target_price": target,
        "reward_risk_ratio": rr,
    }


def score_bucket_3(record: dict[str, Any], cfg: UniverseConfig) -> list[dict[str, Any]]:
    """Score a ticker for Bucket 3 (1-6m+) Fundamental Value Long or Forensic Short."""
    market = record.get("market", {})
    sec = record.get("sec", {})
    fund = sec.get("fundamentals", {})
    dilution = sec.get("dilution", {})

    ticker = str(record["ticker"]).upper()
    close = market.get("close")
    mcap = market.get("market_cap") or 0.0
    ev = market.get("enterprise_value") or mcap
    low_52w = market.get("low_52w")
    is_micro_cap = cfg.min_market_cap <= mcap <= cfg.micro_cap_upper

    ttm_growth = fund.get("ttm_revenue_growth")
    qoq_growth = fund.get("qoq_revenue_growth")
    fcf = fund.get("free_cash_flow")
    ocf = fund.get("operating_cash_flow")
    net_income = fund.get("net_income")
    net_debt = fund.get("net_debt")
    ebitda = fund.get("ebitda")
    accrual_ratio = fund.get("accrual_ratio")
    runway_q = fund.get("cash_runway_quarters")
    altman_z = fund.get("altman_z")

    fcf_yield_ev = (fcf / ev) if (fcf is not None and ev and ev > 0) else None
    net_debt_ebitda = (
        (net_debt / ebitda)
        if (net_debt is not None and ebitda is not None and ebitda > 0)
        else (0.0 if net_debt is not None and net_debt <= 0 else None)
    )
    near_52w_low = bool(low_52w is not None and low_52w > 0 and close <= low_52w * 1.25)

    candidates: list[dict[str, Any]] = []

    # --- LONG: Fundamental Value ---
    pos_growth = bool(
        ttm_growth is not None and ttm_growth > 0
        and (qoq_growth is None or qoq_growth > 0)
    )
    cash_quality = bool(ocf is not None and net_income is not None and ocf > net_income)
    low_leverage = bool(net_debt_ebitda is not None and net_debt_ebitda < 2.5)
    high_fcf_yield = bool(fcf_yield_ev is not None and fcf_yield_ev >= 0.07)

    if pos_growth and high_fcf_yield and cash_quality and low_leverage and near_52w_low and market.get("stabilizing_rs", True):
        score = 70.0 + min((fcf_yield_ev - 0.07) * 200.0, 20.0)
        reasons = [
            f"FCF/EV={fcf_yield_ev * 100:.1f}% (>=7%)",
            f"TTM rev growth={ttm_growth * 100:.1f}%",
            "OCF > Net Income (high cash conversion)",
            f"Net Debt/EBITDA={net_debt_ebitda:.2f}x (<2.5x)",
            "Within 25% of 52w low with stabilizing RS",
        ]
        levels = _build_fundamental_levels("LONG", close, cfg.min_reward_risk_ratio)
        candidates.append(
            {
                "ticker": ticker,
                "bucket": 3,
                "horizon": "1-6m+",
                "direction": "LONG",
                "score": round(score, 2),
                "market_cap": mcap,
                "rvol_20d": round(market.get("rvol_20d") or 0.0, 2),
                "is_micro_cap": is_micro_cap,
                "reasons": reasons,
                "warnings": [],
                "sec_urls": sec.get("sec_urls", []),
                "fundamentals": fund,
                "metrics": market,
                **levels,
            }
        )

    # --- SHORT: Forensic Red Flags ---
    high_accruals = bool(accrual_ratio is not None and accrual_ratio > 0.08)
    runway_dilution = bool(
        runway_q is not None and runway_q < 4.0 and dilution.get("has_active_shelf_90d")
    )
    distress = bool(altman_z is not None and altman_z < 1.8)

    if high_accruals or runway_dilution or distress:
        score = 65.0
        reasons = []
        warnings: list[str] = []

        if high_accruals:
            score += 15.0
            reasons.append(f"Accrual divergence: (NI - OCF)/Assets = {accrual_ratio * 100:.1f}% (>8%)")
        if runway_dilution:
            score += 15.0
            reasons.append(
                f"Cash runway {runway_q:.1f} quarters (<4q) with active shelf ({', '.join(dilution.get('recent_forms', []))})"
            )
        if distress:
            score += 12.0
            reasons.append(f"Balance-sheet distress: Altman Z = {altman_z:.2f} (<1.8)")

        if dilution.get("has_active_shelf_90d"):
            if dilution.get("baby_shelf_constrained"):
                cap = dilution.get("max_baby_shelf_capacity_usd")
                warnings.append(
                    f"BABY-SHELF CAP (<$75M float): S-3 capped at 1/3 float (~${cap:,.0f} over 12m)."
                    if cap else "BABY-SHELF CAP (<$75M float) limits S-3 issuance to 1/3 float/12m."
                )
            else:
                warnings.append("UNCONSTRAINED S-3 OVERHANG (public float >= $75M).")

        levels = _build_fundamental_levels("SHORT", close, cfg.min_reward_risk_ratio)
        candidates.append(
            {
                "ticker": ticker,
                "bucket": 3,
                "horizon": "1-6m+",
                "direction": "SHORT",
                "score": round(score, 2),
                "market_cap": mcap,
                "rvol_20d": round(market.get("rvol_20d") or 0.0, 2),
                "is_micro_cap": is_micro_cap,
                "reasons": reasons,
                "warnings": warnings,
                "sec_urls": sec.get("sec_urls", []),
                "fundamentals": fund,
                "dilution": dilution,
                "metrics": market,
                **levels,
            }
        )

    return candidates
