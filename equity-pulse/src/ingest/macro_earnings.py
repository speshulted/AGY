"""Optional FRED macro regime & Finnhub earnings/estimate-revision connectors."""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import asdict, dataclass
from typing import Any

import requests

from src.config import load_env


@dataclass
class MacroContext:
    as_of: str
    vix: float | None = None
    ten_year_yield: float | None = None
    iwm_close: float | None = None
    iwm_sma50: float | None = None
    iwm_below_sma50: bool = False
    choppy_regime: bool = False
    notes: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fetch_fred_latest(series_id: str, api_key: str, timeout: float = 10.0) -> float | None:
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 5,
    }
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    for obs in resp.json().get("observations", []):
        val = obs.get("value", ".")
        if val != ".":
            try:
                return float(val)
            except ValueError:
                continue
    return None


def fetch_macro_context(
    as_of: dt.date,
    iwm_close: float | None = None,
    iwm_sma50: float | None = None,
) -> MacroContext:
    """Fetch macro regime indicators from FRED when FRED_API_KEY is set; degrade gracefully otherwise."""
    load_env()
    fred_key = os.getenv("FRED_API_KEY", "").strip()
    vix: float | None = None
    dgs10: float | None = None
    notes: list[str] = []

    if fred_key:
        try:
            vix = _fetch_fred_latest("VIXCLS", fred_key)
        except Exception as exc:
            notes.append(f"FRED VIXCLS unavailable: {exc}")
        try:
            dgs10 = _fetch_fred_latest("DGS10", fred_key)
        except Exception as exc:
            notes.append(f"FRED DGS10 unavailable: {exc}")
    else:
        notes.append("FRED_API_KEY not set; using benchmark trend only.")

    iwm_below = bool(iwm_close is not None and iwm_sma50 is not None and iwm_close < iwm_sma50)
    choppy = bool((vix is not None and vix > 25.0) or iwm_below)
    return MacroContext(
        as_of=as_of.isoformat(),
        vix=vix,
        ten_year_yield=dgs10,
        iwm_close=iwm_close,
        iwm_sma50=iwm_sma50,
        iwm_below_sma50=iwm_below,
        choppy_regime=choppy,
        notes=notes,
    )


def fetch_finnhub_earnings_signal(ticker: str, as_of: dt.date, timeout: float = 10.0) -> dict[str, Any]:
    """Optional Finnhub earnings calendar & EPS estimate revision lookup (no-op when key unset)."""
    load_env()
    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not api_key:
        return {"days_to_earnings": None, "eps_revision_direction": "UNKNOWN", "source": "disabled"}

    days_to_earnings: int | None = None
    rev_direction = "NEUTRAL"
    try:
        from_d = as_of.isoformat()
        to_d = (as_of + dt.timedelta(days=45)).isoformat()
        resp = requests.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"symbol": ticker.upper(), "from": from_d, "to": to_d, "token": api_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        events = resp.json().get("earningsCalendar", [])
        if events:
            next_date = dt.date.fromisoformat(events[0]["date"])
            days_to_earnings = (next_date - as_of).days
    except Exception:
        pass

    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/stock/earnings",
            params={"symbol": ticker.upper(), "limit": 2, "token": api_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        items = resp.json()
        if isinstance(items, list) and items:
            surprise = items[0].get("surprisePercent")
            if surprise is not None:
                rev_direction = "UP" if float(surprise) > 0 else "DOWN" if float(surprise) < 0 else "NEUTRAL"
    except Exception:
        pass

    return {
        "days_to_earnings": days_to_earnings,
        "eps_revision_direction": rev_direction,
        "source": "finnhub",
    }
