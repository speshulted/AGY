"""Full US common-equity directory discovery (Nasdaq Trader + SEC EDGAR intersection)."""

from __future__ import annotations

import re
from typing import Any

import requests

from src.config import UniverseConfig, get_sec_user_agent

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# Exchange codes in otherlisted.txt: N = NYSE, A = NYSE American
OTHER_ALLOWED_EXCHANGES = {"N": "NYSE", "A": "NYSE AMERICAN"}

EXCLUDED_NAME_RE = re.compile(
    r"\b(warrant|warrants|right|rights|unit|units|preferred|depositary|notes|"
    r"debenture|etf|etn|trust|fund|acquisition corp|spac|subordinated)\b",
    re.IGNORECASE,
)
COMMON_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}$")


def _is_common_equity(symbol: str, security_name: str, etf_flag: str, test_flag: str) -> bool:
    sym = symbol.strip().upper()
    if not sym or not COMMON_SYMBOL_RE.match(sym):
        return False
    if etf_flag.strip().upper() == "Y" or test_flag.strip().upper() == "Y":
        return False
    if EXCLUDED_NAME_RE.search(security_name or ""):
        return False
    return True


def parse_nasdaq_listed_text(text: str) -> dict[str, dict[str, str]]:
    """Parse Nasdaq Trader nasdaqlisted.txt into {ticker: {exchange, name}} for common equities."""
    out: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("Symbol|") or line.startswith("File Creation Time"):
            continue
        parts = line.split("|")
        if len(parts) < 7:
            continue
        sym, name, _cat, test_issue, _fin_status, _lot, etf = parts[:7]
        if _is_common_equity(sym, name, etf, test_issue):
            out[sym.upper()] = {"exchange": "NASDAQ", "name": name.strip()}
    return out


def parse_other_listed_text(text: str) -> dict[str, dict[str, str]]:
    """Parse Nasdaq Trader otherlisted.txt (NYSE & NYSE American common equities)."""
    out: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("ACT Symbol|") or line.startswith("File Creation Time"):
            continue
        parts = line.split("|")
        if len(parts) < 7:
            continue
        sym, name, exch_code, _cqs, etf, _lot, test_issue = parts[:7]
        exch = OTHER_ALLOWED_EXCHANGES.get(exch_code.strip().upper())
        if not exch:
            continue
        if _is_common_equity(sym, name, etf, test_issue):
            out[sym.upper()] = {"exchange": exch, "name": name.strip()}
    return out


def build_us_equity_universe(
    nasdaq_text: str,
    other_text: str,
    sec_cik_map: dict[str, int] | None,
    cfg: UniverseConfig,
) -> dict[str, dict[str, Any]]:
    """Combine NYSE/NASDAQ/NYSE American common equities, intersect with SEC filers, and drop blocklist."""
    combined = parse_nasdaq_listed_text(nasdaq_text)
    combined.update(parse_other_listed_text(other_text))

    filtered: dict[str, dict[str, Any]] = {}
    for sym, meta in sorted(combined.items()):
        if sym in cfg.do_not_trade:
            continue
        if sec_cik_map is not None and sym not in sec_cik_map:
            continue
        filtered[sym] = {
            "exchange": meta["exchange"],
            "name": meta["name"],
            "cik": sec_cik_map.get(sym) if sec_cik_map is not None else None,
        }
    return filtered


def fetch_us_equity_universe(
    sec_cik_map: dict[str, int] | None,
    cfg: UniverseConfig,
    timeout: float = 20.0,
) -> dict[str, dict[str, Any]]:
    """Download official Nasdaq Trader directories and return the filtered US equity universe."""
    headers = {"User-Agent": get_sec_user_agent()}
    r_nasdaq = requests.get(NASDAQ_LISTED_URL, headers=headers, timeout=timeout)
    r_nasdaq.raise_for_status()
    r_other = requests.get(OTHER_LISTED_URL, headers=headers, timeout=timeout)
    r_other.raise_for_status()
    return build_us_equity_universe(r_nasdaq.text, r_other.text, sec_cik_map, cfg)


def select_pass2_candidates(
    snapshots: dict[str, Any],
    finra_maps: list[dict[str, Any]],
    cfg: UniverseConfig,
    max_enrich: int = 150,
) -> list[dict[str, Any]]:
    """Pass-1 deterministic pre-screener selecting balanced Long & Short candidates for Pass-2 SEC enrichment.

    Filters on price >= min_price and avg_dollar_vol_20d >= min_avg_dollar_vol_20d, then scores
    technical & FINRA triggers across all 3 buckets (both Long and Short) so Pass 2 focuses SEC
    EDGAR rate-limited enrichment on the highest-probability setups across the full US market.
    """
    from src.ingest.finra_short import summarize_short_trends

    long_pool: list[dict[str, Any]] = []
    short_pool: list[dict[str, Any]] = []

    for sym, snap in snapshots.items():
        if sym in cfg.do_not_trade:
            continue
        d = snap.to_dict() if hasattr(snap, "to_dict") else dict(snap)
        close = d.get("close")
        addv = d.get("avg_dollar_vol_20d")
        if close is None or float(close) < cfg.min_price:
            continue
        if addv is None or float(addv) < cfg.min_avg_dollar_vol_20d:
            continue

        sma50 = d.get("sma50")
        sma200 = d.get("sma200")
        ema20 = d.get("ema20")
        high_20d = d.get("high_20d")
        high_55d = d.get("high_55d")
        low_20d = d.get("low_20d")
        low_50d = d.get("low_50d")
        low_52w = d.get("low_52w")
        rvol = float(d.get("rvol_20d") or 0.0)
        gap_pct = float(d.get("gap_pct") or 0.0)
        atr_ext = d.get("atr_extension")

        short_info = summarize_short_trends(finra_maps, sym)
        short_ratio = float(short_info.get("latest_short_vol_ratio") or 0.0)
        rising_short = bool(short_info.get("rising_short_trend"))

        # --- LONG PRE-TRIGGERS (Buckets 1, 2, 3) ---
        long_score = 0.0
        long_tags: list[str] = []

        # Bucket 1 Long: breakout or catalyst gap on elevated RVOL in uptrend
        if (
            sma50 is not None
            and sma200 is not None
            and close > sma50 > sma200
            and (atr_ext is None or atr_ext <= 2.5)
        ):
            if ((high_20d is not None and close > high_20d) or (high_55d is not None and close > high_55d)) and rvol >= 1.8:
                long_score += 50.0 + min(rvol * 5.0, 20.0)
                long_tags.append("b1_breakout_long")
            elif gap_pct > 0.04 and rvol >= 1.8:
                long_score += 45.0 + min(rvol * 4.0, 15.0)
                long_tags.append("b1_gap_long")

        # Bucket 2 Long: short-vol squeeze + 20d EMA reclaim, or strong accumulation
        if ema20 is not None and close > ema20 and d.get("prev_close_below_ema20"):
            if short_ratio > 0.50:
                long_score += 42.0 + (short_ratio - 0.50) * 40.0
                long_tags.append("b2_squeeze_reclaim_long")
            elif rvol >= 1.5:
                long_score += 24.0
                long_tags.append("b2_ema20_reclaim_long")

        # Bucket 3 Long: within 25% of 52-week low with stabilizing relative strength
        if low_52w is not None and low_52w > 0 and close <= low_52w * 1.25 and d.get("stabilizing_rs"):
            long_score += 32.0
            long_tags.append("b3_value_base_long")

        if long_score > 0:
            long_pool.append({"ticker": sym, "side": "LONG", "pass1_score": round(long_score, 2), "tags": long_tags})

        # --- SHORT PRE-TRIGGERS (Buckets 1, 2, 3) ---
        short_score = 0.0
        short_tags: list[str] = []

        # Bucket 1 Short: support breakdown below 20d/50d low on heavy RVOL under SMA50
        if sma50 is not None and close < sma50:
            if ((low_20d is not None and close < low_20d) or (low_50d is not None and close < low_50d)) and rvol >= 1.8:
                short_score += 50.0 + min(rvol * 5.0, 20.0)
                short_tags.append("b1_breakdown_short")

            # Bucket 2 Short: lower high below SMA50 + rising FINRA short-volume trend
            if d.get("has_lower_high_under_sma50") and rising_short:
                short_score += 40.0 + (short_ratio * 10.0)
                short_tags.append("b2_dist_roll_short")

            # Bucket 3 Short: persistent structural downtrend below SMA50 & SMA200
            if sma200 is not None and close < sma50 < sma200 and not d.get("stabilizing_rs"):
                short_score += 28.0
                short_tags.append("b3_forensic_downtrend_short")

        if short_score > 0:
            short_pool.append({"ticker": sym, "side": "SHORT", "pass1_score": round(short_score, 2), "tags": short_tags})

    long_pool.sort(key=lambda x: x["pass1_score"], reverse=True)
    short_pool.sort(key=lambda x: x["pass1_score"], reverse=True)

    # Balance Pass-2 enrichment slots 50/50 between Long and Short triggers (filling any unused quota from the other side)
    half = max(1, max_enrich // 2)
    chosen: dict[str, dict[str, Any]] = {}

    for item in long_pool[:half]:
        chosen[item["ticker"]] = item
    for item in short_pool[: max_enrich - len(chosen)]:
        if item["ticker"] not in chosen:
            chosen[item["ticker"]] = item
        else:
            chosen[item["ticker"]]["tags"] = list(dict.fromkeys(chosen[item["ticker"]]["tags"] + item["tags"]))

    # Backfill if still below max_enrich
    if len(chosen) < max_enrich:
        for item in long_pool[half:] + short_pool[half:]:
            if len(chosen) >= max_enrich:
                break
            if item["ticker"] not in chosen:
                chosen[item["ticker"]] = item

    return sorted(chosen.values(), key=lambda x: x["pass1_score"], reverse=True)
