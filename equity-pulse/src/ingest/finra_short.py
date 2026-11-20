"""FINRA Reg SHO Daily Short Sale Volume & SEC Fails-to-Deliver (FTD) parsers."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass
from typing import Any

import requests

from src.config import get_sec_user_agent

FINRA_REGSHO_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{yyyymmdd}.txt"


@dataclass
class FinraShortRecord:
    date: str
    symbol: str
    short_volume: int
    short_exempt_volume: int
    total_volume: int
    short_vol_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FtdRecord:
    settlement_date: str
    cusip: str
    symbol: str
    quantity_fails: int
    price: float
    notional_usd: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_finra_regsho_text(text: str) -> dict[str, FinraShortRecord]:
    """Parse FINRA CNMSshvolYYYYMMDD.txt pipe-delimited content into a symbol map."""
    records: dict[str, FinraShortRecord] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("Date|"):
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        date_str, symbol = parts[0].strip(), parts[1].strip().upper()
        try:
            short_vol = int(parts[2].strip())
            exempt_vol = int(parts[3].strip())
            total_vol = int(parts[4].strip())
        except ValueError:
            continue
        if total_vol <= 0:
            continue
        ratio = short_vol / total_vol
        records[symbol] = FinraShortRecord(
            date=date_str,
            symbol=symbol,
            short_volume=short_vol,
            short_exempt_volume=exempt_vol,
            total_volume=total_vol,
            short_vol_ratio=round(ratio, 4),
        )
    return records


def parse_sec_ftd_text(text: str) -> dict[str, list[FtdRecord]]:
    """Parse biweekly SEC Fails-to-Deliver pipe-delimited text grouped by symbol."""
    by_symbol: dict[str, list[FtdRecord]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.upper().startswith("SETTLEMENT DATE|"):
            continue
        parts = line.split("|")
        if len(parts) < 6:
            continue
        sdate = parts[0].strip()
        cusip = parts[1].strip()
        symbol = parts[2].strip().upper()
        try:
            qty = int(parts[3].strip())
            price = float(parts[5].strip())
        except ValueError:
            continue
        rec = FtdRecord(
            settlement_date=sdate,
            cusip=cusip,
            symbol=symbol,
            quantity_fails=qty,
            price=price,
            notional_usd=round(qty * price, 2),
        )
        by_symbol.setdefault(symbol, []).append(rec)
    return by_symbol


def summarize_short_trends(
    daily_maps: list[dict[str, FinraShortRecord]],
    symbol: str,
    ftd_records: list[FtdRecord] | None = None,
    float_shares: float | None = None,
) -> dict[str, Any]:
    """Compute latest short-vol ratio, 5d vs 20d short-vol trend, and FTD spike flag.

    `daily_maps` should be ordered newest-first (index 0 = most recent trading day).
    """
    sym = symbol.upper()
    ratios = [m[sym].short_vol_ratio for m in daily_maps if sym in m]
    latest_ratio = ratios[0] if ratios else None
    avg_5d = sum(ratios[:5]) / len(ratios[:5]) if ratios[:5] else None
    avg_20d = sum(ratios[:20]) / len(ratios[:20]) if ratios[:20] else None
    rising_short_trend = bool(avg_5d is not None and avg_20d is not None and avg_5d > avg_20d)

    latest_ftd_qty = 0
    ftd_spike = False
    if ftd_records:
        ordered = sorted(ftd_records, key=lambda r: r.settlement_date)
        latest_ftd_qty = ordered[-1].quantity_fails
        mean_qty = sum(r.quantity_fails for r in ordered) / len(ordered)
        pct_float = (latest_ftd_qty / float_shares) if float_shares and float_shares > 0 else 0.0
        ftd_spike = bool(
            (latest_ftd_qty >= 50_000 and latest_ftd_qty >= 2.0 * max(mean_qty, 1.0))
            or pct_float >= 0.005
        )

    return {
        "latest_short_vol_ratio": round(latest_ratio, 4) if latest_ratio is not None else None,
        "short_vol_ratio_5d": round(avg_5d, 4) if avg_5d is not None else None,
        "short_vol_ratio_20d": round(avg_20d, 4) if avg_20d is not None else None,
        "rising_short_trend": rising_short_trend,
        "latest_ftd_shares": latest_ftd_qty,
        "ftd_spike": ftd_spike,
        "short_or_ftd_spike": bool((latest_ratio is not None and latest_ratio >= 0.55) or ftd_spike),
    }


def fetch_recent_finra_regsho(
    as_of: dt.date,
    lookback_trading_days: int = 20,
    timeout: float = 15.0,
) -> list[dict[str, FinraShortRecord]]:
    """Fetch up to `lookback_trading_days` of FINRA Reg SHO daily files (newest first)."""
    session = requests.Session()
    session.headers.update({"User-Agent": get_sec_user_agent()})

    daily_maps: list[dict[str, FinraShortRecord]] = []
    cursor = as_of
    attempts = 0
    max_calendar_days = lookback_trading_days * 2 + 10

    while len(daily_maps) < lookback_trading_days and attempts < max_calendar_days:
        attempts += 1
        if cursor.weekday() < 5:
            url = FINRA_REGSHO_URL.format(yyyymmdd=cursor.strftime("%Y%m%d"))
            try:
                resp = session.get(url, timeout=timeout)
                if resp.status_code == 200 and "Date|Symbol|" in resp.text:
                    parsed = parse_finra_regsho_text(resp.text)
                    if parsed:
                        daily_maps.append(parsed)
            except Exception:
                pass
        cursor -= dt.timedelta(days=1)

    return daily_maps
