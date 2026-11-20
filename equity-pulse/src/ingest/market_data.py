"""Batched OHLCV market data ingestion (yfinance primary + Stooq CSV fallback) & SQLite cache."""

from __future__ import annotations

import datetime as dt
import io
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from src.config import CACHE_DB_PATH


@dataclass
class MarketSnapshot:
    ticker: str
    as_of: str
    close: float
    prev_close: float
    gap_pct: float
    sma20: float | None
    ema20: float | None
    sma50: float | None
    sma200: float | None
    high_20d: float | None
    low_20d: float | None
    high_55d: float | None
    low_50d: float | None
    high_52w: float | None
    low_52w: float | None
    atr14: float | None
    atr_extension: float | None
    avg_vol_20d: float | None
    rvol_20d: float | None
    avg_dollar_vol_20d: float | None
    market_cap: float | None = None
    float_shares: float | None = None
    enterprise_value: float | None = None
    exchange: str = ""
    quote_type: str = "EQUITY"
    days_to_earnings: int | None = None
    prev_close_below_ema20: bool = False
    has_lower_high_under_sma50: bool = False
    stabilizing_rs: bool = False
    failed_earnings_reaction: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_ohlcv_metrics(
    ticker: str,
    df: pd.DataFrame,
    meta: dict[str, Any] | None = None,
) -> MarketSnapshot | None:
    """Compute technical indicators and liquidity metrics from a daily OHLCV DataFrame."""
    if df is None or df.empty or len(df) < 2:
        return None

    frame = df.copy()
    frame.columns = [str(c).strip().title() for c in frame.columns]
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in frame.columns:
            return None
    frame = frame.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    if len(frame) < 2:
        return None

    close_s = frame["Close"].astype(float)
    open_s = frame["Open"].astype(float)
    high_s = frame["High"].astype(float)
    low_s = frame["Low"].astype(float)
    vol_s = frame["Volume"].astype(float)

    latest_close = float(close_s.iloc[-1])
    prev_close = float(close_s.iloc[-2])
    latest_open = float(open_s.iloc[-1])
    gap_pct = (latest_open - prev_close) / prev_close if prev_close > 0 else 0.0

    sma20 = float(close_s.rolling(20).mean().iloc[-1]) if len(close_s) >= 20 else None
    ema20_series = close_s.ewm(span=20, adjust=False).mean()
    ema20 = float(ema20_series.iloc[-1]) if len(close_s) >= 20 else None
    sma50 = float(close_s.rolling(50).mean().iloc[-1]) if len(close_s) >= 50 else None
    sma200 = float(close_s.rolling(200).mean().iloc[-1]) if len(close_s) >= 200 else None

    # Prior N-day highs/lows (excluding today so breakouts compare close vs prior range)
    prior_high = high_s.iloc[:-1]
    prior_low = low_s.iloc[:-1]
    high_20d = float(prior_high.tail(20).max()) if len(prior_high) >= 20 else None
    low_20d = float(prior_low.tail(20).min()) if len(prior_low) >= 20 else None
    high_55d = float(prior_high.tail(55).max()) if len(prior_high) >= 55 else None
    low_50d = float(prior_low.tail(50).min()) if len(prior_low) >= 50 else None
    high_52w = float(high_s.tail(252).max()) if len(high_s) >= 20 else None
    low_52w = float(low_s.tail(252).min()) if len(low_s) >= 20 else None

    # True Range & ATR14
    prev_c = close_s.shift(1)
    tr = pd.concat(
        [
            high_s - low_s,
            (high_s - prev_c).abs(),
            (low_s - prev_c).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else None

    atr_extension = None
    if sma20 is not None and atr14 is not None and atr14 > 0:
        atr_extension = (latest_close - sma20) / atr14

    avg_vol_20d = float(vol_s.tail(20).mean()) if len(vol_s) >= 20 else float(vol_s.mean())
    rvol_20d = float(vol_s.iloc[-1] / avg_vol_20d) if avg_vol_20d and avg_vol_20d > 0 else None
    dollar_vol = close_s * vol_s
    avg_dollar_vol_20d = float(dollar_vol.tail(20).mean()) if len(dollar_vol) >= 20 else float(dollar_vol.mean())

    prev_close_below_ema20 = bool(
        ema20 is not None and len(close_s) >= 2 and float(close_s.iloc[-2]) < float(ema20_series.iloc[-2])
    )

    has_lower_high_under_sma50 = False
    if sma50 is not None and len(high_s) >= 20 and latest_close < sma50:
        recent_peak = float(high_s.tail(10).max())
        prior_peak = float(high_s.tail(20).head(10).max())
        has_lower_high_under_sma50 = recent_peak < prior_peak

    stabilizing_rs = False
    if len(close_s) >= 20:
        ret_20d = (latest_close - float(close_s.iloc[-20])) / max(float(close_s.iloc[-20]), 1e-6)
        stabilizing_rs = ret_20d >= -0.02

    as_of_idx = frame.index[-1]
    as_of_str = as_of_idx.strftime("%Y-%m-%d") if hasattr(as_of_idx, "strftime") else str(as_of_idx)[:10]

    info = meta or {}
    return MarketSnapshot(
        ticker=ticker.upper(),
        as_of=as_of_str,
        close=round(latest_close, 4),
        prev_close=round(prev_close, 4),
        gap_pct=round(gap_pct, 4),
        sma20=round(sma20, 4) if sma20 is not None else None,
        ema20=round(ema20, 4) if ema20 is not None else None,
        sma50=round(sma50, 4) if sma50 is not None else None,
        sma200=round(sma200, 4) if sma200 is not None else None,
        high_20d=round(high_20d, 4) if high_20d is not None else None,
        low_20d=round(low_20d, 4) if low_20d is not None else None,
        high_55d=round(high_55d, 4) if high_55d is not None else None,
        low_50d=round(low_50d, 4) if low_50d is not None else None,
        high_52w=round(high_52w, 4) if high_52w is not None else None,
        low_52w=round(low_52w, 4) if low_52w is not None else None,
        atr14=round(atr14, 4) if atr14 is not None else None,
        atr_extension=round(atr_extension, 4) if atr_extension is not None else None,
        avg_vol_20d=round(avg_vol_20d, 2) if avg_vol_20d is not None else None,
        rvol_20d=round(rvol_20d, 4) if rvol_20d is not None else None,
        avg_dollar_vol_20d=round(avg_dollar_vol_20d, 2) if avg_dollar_vol_20d is not None else None,
        market_cap=info.get("market_cap"),
        float_shares=info.get("float_shares"),
        enterprise_value=info.get("enterprise_value"),
        exchange=str(info.get("exchange", "")).upper(),
        quote_type=str(info.get("quote_type", "EQUITY")).upper(),
        days_to_earnings=info.get("days_to_earnings"),
        prev_close_below_ema20=prev_close_below_ema20,
        has_lower_high_under_sma50=has_lower_high_under_sma50,
        stabilizing_rs=stabilizing_rs,
        failed_earnings_reaction=bool(info.get("failed_earnings_reaction", False)),
    )


def fetch_stooq_ohlcv(ticker: str, timeout: float = 15.0) -> pd.DataFrame:
    """Fallback daily OHLCV loader via Stooq CSV endpoint."""
    url = f"https://stooq.com/q/d/l/?s={ticker.lower()}.us&i=d"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    if "Date,Open,High,Low,Close,Volume" not in resp.text:
        return pd.DataFrame()
    df = pd.read_csv(io.StringIO(resp.text), parse_dates=["Date"]).set_index("Date").sort_index()
    return df.tail(260)


def fetch_batch_market_snapshots(
    tickers: list[str],
    period: str = "1y",
) -> dict[str, MarketSnapshot]:
    """Fetch OHLCV & metadata in batches via yfinance with Stooq fallback."""
    import os
    import yfinance as yf  # lazy import for fast test startup

    clean = list(dict.fromkeys(t.upper() for t in tickers if t))
    if not clean:
        return {}

    yf_session = None
    ca_bundle = os.environ.get("CURL_CA_BUNDLE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if ca_bundle:
        try:
            from curl_cffi import requests as curl_requests

            yf_session = curl_requests.Session(impersonate="chrome", verify=ca_bundle)
        except Exception:
            yf_session = None

    snapshots: dict[str, MarketSnapshot] = {}
    try:
        dl_kwargs: dict[str, Any] = {
            "tickers": clean,
            "period": period,
            "interval": "1d",
            "group_by": "ticker",
            "auto_adjust": False,
            "progress": False,
            "threads": False,
        }
        if yf_session is not None:
            dl_kwargs["session"] = yf_session
        raw = yf.download(**dl_kwargs)
    except Exception:
        raw = pd.DataFrame()

    for sym in clean:
        df = pd.DataFrame()
        if not raw.empty:
            if isinstance(raw.columns, pd.MultiIndex):
                if sym in raw.columns.get_level_values(0):
                    df = raw[sym].dropna(how="all")
            elif len(clean) == 1:
                df = raw.dropna(how="all")

        if df.empty:
            try:
                df = fetch_stooq_ohlcv(sym)
            except Exception:
                df = pd.DataFrame()

        meta: dict[str, Any] = {}
        try:
            tk = yf.Ticker(sym, session=yf_session) if yf_session is not None else yf.Ticker(sym)
            info = tk.info or {}
            meta = {
                "market_cap": info.get("marketCap"),
                "float_shares": info.get("floatShares"),
                "enterprise_value": info.get("enterpriseValue"),
                "exchange": info.get("exchange", ""),
                "quote_type": info.get("quoteType", "EQUITY"),
            }
            cal = tk.calendar
            if isinstance(cal, dict) and cal.get("Earnings Date"):
                ed = cal["Earnings Date"][0]
                if hasattr(ed, "date"):
                    ed = ed.date()
                if isinstance(ed, dt.date):
                    meta["days_to_earnings"] = (ed - dt.date.today()).days
        except Exception:
            pass

        snap = compute_ohlcv_metrics(sym, df, meta=meta)
        if snap is not None:
            snapshots[sym] = snap

    return snapshots


class MarketCacheStore:
    """SQLite cache (`data/market_cache.db`) storing ingested snapshots per run date."""

    def __init__(self, db_path: Path = CACHE_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_snapshots (
                    run_date TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_date, ticker)
                )
                """
            )
            conn.commit()

    def upsert_record(self, run_date: str, ticker: str, payload: dict[str, Any]) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_snapshots (run_date, ticker, payload_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_date, ticker) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (run_date, ticker.upper(), json.dumps(payload), now),
            )
            conn.commit()

    def load_records(self, run_date: str) -> dict[str, dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ticker, payload_json FROM daily_snapshots WHERE run_date = ?",
                (run_date,),
            ).fetchall()
        return {ticker: json.loads(blob) for ticker, blob in rows}
