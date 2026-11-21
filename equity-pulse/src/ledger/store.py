"""SQLite Signal Ledger, cooldown enforcement, forward alpha grading, and 30d Scorecard."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from src.config import LEDGER_DB_PATH


def signed_return(direction: str, p0: float, pt: float) -> float:
    """Direction-adjusted return: d * (Pt - P0) / P0 (+1 LONG, -1 SHORT)."""
    if p0 <= 0:
        raise ValueError("Reference price p0 must be positive.")
    d = 1.0 if direction.upper() == "LONG" else -1.0
    return d * ((pt - p0) / p0)


def _trading_days_between(start: dt.date, end: dt.date) -> int:
    if end <= start:
        return 0
    days = 0
    cur = start + dt.timedelta(days=1)
    while cur <= end:
        if cur.weekday() < 5:
            days += 1
        cur += dt.timedelta(days=1)
    return days


class SignalLedger:
    """Persistent SQLite ledger storing surfaced signals and auto-graded forward outcomes."""

    def __init__(self, db_path: Path = LEDGER_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_date TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK(direction IN ('LONG', 'SHORT')),
                    bucket INTEGER NOT NULL CHECK(bucket IN (1, 2, 3)),
                    market_cap REAL,
                    ref_price REAL NOT NULL,
                    entry_low REAL NOT NULL,
                    entry_high REAL NOT NULL,
                    stop_price REAL NOT NULL,
                    target_price REAL NOT NULL,
                    reward_risk_ratio REAL NOT NULL,
                    conviction INTEGER NOT NULL DEFAULT 3 CHECK(conviction BETWEEN 1 AND 5),
                    catalyst_summary TEXT DEFAULT '',
                    sec_urls_json TEXT DEFAULT '[]',
                    critic_warnings TEXT DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_signals_ticker_bucket_date
                    ON signals(ticker, bucket, run_date);

                CREATE TABLE IF NOT EXISTS signal_grades (
                    signal_id INTEGER PRIMARY KEY REFERENCES signals(id) ON DELETE CASCADE,
                    ret_1d REAL,
                    ret_5d REAL,
                    ret_20d REAL,
                    ret_60d REAL,
                    alpha_spy_20d REAL,
                    alpha_iwm_20d REAL,
                    max_favorable_excursion REAL,
                    max_adverse_excursion REAL,
                    latest_price REAL,
                    latest_date TEXT,
                    latest_ret REAL,
                    outcome_status TEXT NOT NULL DEFAULT 'OPEN'
                        CHECK(outcome_status IN ('OPEN', 'HIT_TARGET', 'STOPPED_OUT', 'TIME_EXPIRED')),
                    graded_at TEXT NOT NULL
                );
                """
            )
            # Non-destructive migration for existing SQLite databases
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(signal_grades)").fetchall()}
            if "latest_price" not in cols:
                conn.execute("ALTER TABLE signal_grades ADD COLUMN latest_price REAL")
            if "latest_date" not in cols:
                conn.execute("ALTER TABLE signal_grades ADD COLUMN latest_date TEXT")
            if "latest_ret" not in cols:
                conn.execute("ALTER TABLE signal_grades ADD COLUMN latest_ret REAL")
            conn.commit()

    def record_signal(self, signal: dict[str, Any]) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO signals (
                    run_date, ticker, direction, bucket, market_cap, ref_price,
                    entry_low, entry_high, stop_price, target_price, reward_risk_ratio,
                    conviction, catalyst_summary, sec_urls_json, critic_warnings
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal["run_date"],
                    str(signal["ticker"]).upper(),
                    str(signal["direction"]).upper(),
                    int(signal["bucket"]),
                    signal.get("market_cap"),
                    float(signal["ref_price"]),
                    float(signal["entry_low"]),
                    float(signal["entry_high"]),
                    float(signal["stop_price"]),
                    float(signal["target_price"]),
                    float(signal["reward_risk_ratio"]),
                    int(signal.get("conviction", 3)),
                    str(signal.get("catalyst_summary", "")),
                    json.dumps(signal.get("sec_urls", [])),
                    str(signal.get("critic_warnings", "")),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def is_on_cooldown(
        self,
        ticker: str,
        bucket: int,
        as_of: dt.date,
        cooldown_days: int,
        has_recent_sec_filing: bool = False,
    ) -> bool:
        """Return True if ticker was surfaced in this bucket within `cooldown_days` trading days,
        unless overridden by a fresh SEC 8-K or Form 4 in the last 24 hours.
        """
        if has_recent_sec_filing:
            return False
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_date FROM signals
                WHERE ticker = ? AND bucket = ?
                ORDER BY run_date DESC LIMIT 1
                """,
                (ticker.upper(), int(bucket)),
            ).fetchone()
        if row is None:
            return False
        last_date = dt.date.fromisoformat(row["run_date"])
        elapsed_td = _trading_days_between(last_date, as_of)
        return elapsed_td < cooldown_days

    def list_signals(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM signals ORDER BY run_date DESC, id DESC").fetchall()
        return [dict(r) for r in rows]

    def grade_ledger(
        self,
        series_fetcher: Callable[[str, str], list[dict[str, Any]]],
        as_of: dt.date,
    ) -> int:
        """Grade all signals using OHLCV series fetched from `run_date` to `as_of`.

        `series_fetcher(ticker, start_date_iso)` returns chronological daily bars:
        `[{"date": "YYYY-MM-DD", "high": float, "low": float, "close": float}, ...]`.
        """
        signals = self.list_signals()
        if not signals:
            return 0

        earliest = min(s["run_date"] for s in signals)
        spy_bars = series_fetcher("SPY", earliest)
        iwm_bars = series_fetcher("IWM", earliest)
        spy_by_date = {b["date"]: float(b["close"]) for b in spy_bars}
        iwm_by_date = {b["date"]: float(b["close"]) for b in iwm_bars}

        graded_count = 0
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
        horizon_expiry_days = {1: 5, 2: 40, 3: 120}

        with self._connect() as conn:
            for sig in signals:
                ticker = sig["ticker"]
                direction = sig["direction"]
                run_date = sig["run_date"]
                p0 = float(sig["ref_price"])
                stop = float(sig["stop_price"])
                target = float(sig["target_price"])

                bars = [b for b in series_fetcher(ticker, run_date) if b["date"] > run_date]
                if not bars:
                    continue

                def _ret_at(idx: int) -> float | None:
                    if len(bars) >= idx:
                        return round(signed_return(direction, p0, float(bars[idx - 1]["close"])), 6)
                    return None

                ret_1d = _ret_at(1)
                ret_5d = _ret_at(5)
                ret_20d = _ret_at(20)
                ret_60d = _ret_at(60)

                # Benchmark 20d alpha
                alpha_spy_20d = None
                alpha_iwm_20d = None
                if ret_20d is not None:
                    d20 = bars[19]["date"]
                    spy_0 = spy_by_date.get(run_date)
                    spy_20 = spy_by_date.get(d20)
                    if spy_0 and spy_20:
                        bench_spy = (spy_20 - spy_0) / spy_0
                        alpha_spy_20d = round(ret_20d - bench_spy, 6)
                    iwm_0 = iwm_by_date.get(run_date)
                    iwm_20 = iwm_by_date.get(d20)
                    if iwm_0 and iwm_20:
                        bench_iwm = (iwm_20 - iwm_0) / iwm_0
                        alpha_iwm_20d = round(ret_20d - bench_iwm, 6)

                # MFE / MAE & outcome status
                outcome = "OPEN"
                mfe = 0.0
                mae = 0.0
                for b in bars:
                    hi = float(b["high"])
                    lo = float(b["low"])
                    if direction == "LONG":
                        fav = (hi - p0) / p0
                        adv = (lo - p0) / p0
                        if outcome == "OPEN":
                            if lo <= stop:
                                outcome = "STOPPED_OUT"
                            elif hi >= target:
                                outcome = "HIT_TARGET"
                    else:
                        fav = (p0 - lo) / p0
                        adv = (p0 - hi) / p0
                        if outcome == "OPEN":
                            if hi >= stop:
                                outcome = "STOPPED_OUT"
                            elif lo <= target:
                                outcome = "HIT_TARGET"
                    mfe = max(mfe, fav)
                    mae = min(mae, adv)

                if outcome == "OPEN" and len(bars) >= horizon_expiry_days.get(int(sig["bucket"]), 20):
                    outcome = "TIME_EXPIRED"

                latest_bar = bars[-1]
                latest_price = round(float(latest_bar["close"]), 4)
                latest_date = str(latest_bar["date"])
                latest_ret = round(signed_return(direction, p0, latest_price), 6)

                conn.execute(
                    """
                    INSERT INTO signal_grades (
                        signal_id, ret_1d, ret_5d, ret_20d, ret_60d,
                        alpha_spy_20d, alpha_iwm_20d, max_favorable_excursion,
                        max_adverse_excursion, latest_price, latest_date, latest_ret,
                        outcome_status, graded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(signal_id) DO UPDATE SET
                        ret_1d = excluded.ret_1d,
                        ret_5d = excluded.ret_5d,
                        ret_20d = excluded.ret_20d,
                        ret_60d = excluded.ret_60d,
                        alpha_spy_20d = excluded.alpha_spy_20d,
                        alpha_iwm_20d = excluded.alpha_iwm_20d,
                        max_favorable_excursion = excluded.max_favorable_excursion,
                        max_adverse_excursion = excluded.max_adverse_excursion,
                        latest_price = excluded.latest_price,
                        latest_date = excluded.latest_date,
                        latest_ret = excluded.latest_ret,
                        outcome_status = excluded.outcome_status,
                        graded_at = excluded.graded_at
                    """,
                    (
                        sig["id"],
                        ret_1d,
                        ret_5d,
                        ret_20d,
                        ret_60d,
                        alpha_spy_20d,
                        alpha_iwm_20d,
                        round(mfe, 6),
                        round(mae, 6),
                        latest_price,
                        latest_date,
                        latest_ret,
                        outcome,
                        now_iso,
                    ),
                )
                graded_count += 1
            conn.commit()
        return graded_count

    def get_previous_day_accuracy(self, as_of: dt.date) -> dict[str, Any]:
        """Analyze next-day accuracy of the most recent prior run_date (< as_of)."""
        as_of_iso = as_of.isoformat()
        with self._connect() as conn:
            prev_row = conn.execute(
                "SELECT MAX(run_date) AS prev_date FROM signals WHERE run_date < ?",
                (as_of_iso,),
            ).fetchone()
            prev_date = prev_row["prev_date"] if prev_row and prev_row["prev_date"] else None
            if not prev_date:
                return {
                    "as_of": as_of_iso,
                    "previous_run_date": None,
                    "total_recommendations": 0,
                    "evaluated_count": 0,
                    "hit_rate_pct": None,
                    "profit_factor": None,
                    "avg_signed_return_pct": None,
                    "long_accuracy": {"count": 0, "hits": 0, "hit_rate_pct": None},
                    "short_accuracy": {"count": 0, "hits": 0, "hit_rate_pct": None},
                    "items": [],
                    "plain_english_summary": "No prior-day recommendations recorded yet—today's picks will be graded automatically on the next trading day.",
                }

            rows = conn.execute(
                """
                SELECT s.id, s.run_date, s.ticker, s.direction, s.bucket, s.ref_price,
                       s.stop_price, s.target_price,
                       g.ret_1d, g.latest_price, g.latest_date, g.latest_ret,
                       g.outcome_status
                FROM signals s
                LEFT JOIN signal_grades g ON g.signal_id = s.id
                WHERE s.run_date = ?
                ORDER BY s.id ASC
                """,
                (prev_date,),
            ).fetchall()

        items: list[dict[str, Any]] = []
        evaluated_rets: list[float] = []
        long_rets: list[float] = []
        short_rets: list[float] = []

        for r in rows:
            next_ret = r["ret_1d"] if r["ret_1d"] is not None else r["latest_ret"]
            verdict = "PENDING"
            if next_ret is not None:
                ret_f = float(next_ret)
                evaluated_rets.append(ret_f)
                if r["direction"] == "LONG":
                    long_rets.append(ret_f)
                else:
                    short_rets.append(ret_f)
                verdict = "CORRECT" if ret_f > 0 else ("FLAT" if ret_f == 0 else "INCORRECT")

            items.append(
                {
                    "signal_id": int(r["id"]),
                    "ticker": r["ticker"],
                    "direction": r["direction"],
                    "bucket": int(r["bucket"]),
                    "ref_price": round(float(r["ref_price"]), 2),
                    "latest_price": round(float(r["latest_price"]), 2) if r["latest_price"] is not None else None,
                    "latest_date": r["latest_date"],
                    "next_day_return_pct": round(float(next_ret) * 100.0, 2) if next_ret is not None else None,
                    "verdict": verdict,
                    "outcome_status": r["outcome_status"] or "OPEN",
                }
            )

        hits = sum(1 for x in evaluated_rets if x > 0)
        hit_rate = round((hits / len(evaluated_rets)) * 100.0, 1) if evaluated_rets else None
        avg_ret = round((sum(evaluated_rets) / len(evaluated_rets)) * 100.0, 2) if evaluated_rets else None

        gross_gains = sum(x for x in evaluated_rets if x > 0)
        gross_losses = abs(sum(x for x in evaluated_rets if x < 0))
        if not evaluated_rets:
            profit_factor = None
        elif gross_losses == 0:
            profit_factor = round(gross_gains * 100.0, 2) if gross_gains > 0 else 0.0
        else:
            profit_factor = round(gross_gains / gross_losses, 2)

        long_hits = sum(1 for x in long_rets if x > 0)
        short_hits = sum(1 for x in short_rets if x > 0)

        if evaluated_rets:
            summary = (
                f"From {prev_date}'s {len(items)} recommendations, {hits}/{len(evaluated_rets)} moved in the "
                f"predicted direction ({hit_rate:.1f}% hit rate, avg signed return {avg_ret:+.2f}%, "
                f"profit factor {profit_factor})."
            )
        else:
            summary = (
                f"{len(items)} recommendations from {prev_date} are logged and awaiting next-day closing prices to grade."
            )

        return {
            "as_of": as_of_iso,
            "previous_run_date": prev_date,
            "total_recommendations": len(items),
            "evaluated_count": len(evaluated_rets),
            "hits": hits,
            "hit_rate_pct": hit_rate,
            "profit_factor": profit_factor,
            "avg_signed_return_pct": avg_ret,
            "long_accuracy": {
                "count": len(long_rets),
                "hits": long_hits,
                "hit_rate_pct": round((long_hits / len(long_rets)) * 100.0, 1) if long_rets else None,
            },
            "short_accuracy": {
                "count": len(short_rets),
                "hits": short_hits,
                "hit_rate_pct": round((short_hits / len(short_rets)) * 100.0, 1) if short_rets else None,
            },
            "items": items,
            "plain_english_summary": summary,
        }

    def get_30d_scorecard(self, as_of: dt.date) -> dict[str, Any]:
        """Emit a compact 30-day Scorecard per bucket (Win %, Avg R-Multiple, Mean +5d/+20d Alpha vs IWM)."""
        cutoff = (as_of - dt.timedelta(days=30)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.bucket, s.reward_risk_ratio, g.ret_5d, g.ret_20d,
                       g.alpha_iwm_20d, g.outcome_status
                FROM signals s
                LEFT JOIN signal_grades g ON g.signal_id = s.id
                WHERE s.run_date >= ?
                """,
                (cutoff,),
            ).fetchall()

        by_bucket: dict[str, dict[str, Any]] = {}
        for bucket in (1, 2, 3):
            b_rows = [r for r in rows if int(r["bucket"]) == bucket]
            closed = [r for r in b_rows if r["outcome_status"] in {"HIT_TARGET", "STOPPED_OUT", "TIME_EXPIRED"}]
            wins = [r for r in closed if r["outcome_status"] == "HIT_TARGET" or (r["outcome_status"] == "TIME_EXPIRED" and (r["ret_5d"] or 0) > 0)]
            r_mults = []
            for r in closed:
                if r["outcome_status"] == "HIT_TARGET":
                    r_mults.append(float(r["reward_risk_ratio"]))
                elif r["outcome_status"] == "STOPPED_OUT":
                    r_mults.append(-1.0)
            ret5s = [float(r["ret_5d"]) for r in b_rows if r["ret_5d"] is not None]
            alpha20s = [float(r["alpha_iwm_20d"]) for r in b_rows if r["alpha_iwm_20d"] is not None]

            by_bucket[f"bucket_{bucket}"] = {
                "total_signals": len(b_rows),
                "closed_signals": len(closed),
                "win_pct": round((len(wins) / len(closed)) * 100.0, 1) if closed else None,
                "avg_r_multiple": round(sum(r_mults) / len(r_mults), 2) if r_mults else None,
                "mean_ret_5d_pct": round((sum(ret5s) / len(ret5s)) * 100.0, 2) if ret5s else None,
                "mean_alpha_iwm_20d_pct": round((sum(alpha20s) / len(alpha20s)) * 100.0, 2) if alpha20s else None,
            }

        return {
            "window_days": 30,
            "as_of": as_of.isoformat(),
            "buckets": by_bucket,
        }
