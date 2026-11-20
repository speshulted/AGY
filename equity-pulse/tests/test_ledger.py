"""Hermetic tests for SQLite Signal Ledger, signed returns, alpha vs SPY/IWM, and 30d Scorecard."""

import datetime as dt
from pathlib import Path

from src.ledger.store import SignalLedger, signed_return


def test_signed_return_long_and_short() -> None:
    assert round(signed_return("LONG", 100.0, 110.0), 4) == 0.10
    assert round(signed_return("SHORT", 100.0, 90.0), 4) == 0.10
    assert round(signed_return("SHORT", 100.0, 110.0), 4) == -0.10


def test_ledger_grading_alpha_and_scorecard(tmp_path: Path) -> None:
    ledger = SignalLedger(tmp_path / "signal_ledger.db")
    ledger.record_signal(
        {
            "run_date": "2026-10-01",
            "ticker": "WINR",
            "direction": "LONG",
            "bucket": 1,
            "market_cap": 500_000_000,
            "ref_price": 100.0,
            "entry_low": 99.0,
            "entry_high": 101.0,
            "stop_price": 95.0,
            "target_price": 112.0,
            "reward_risk_ratio": 2.4,
            "conviction": 4,
        }
    )
    ledger.record_signal(
        {
            "run_date": "2026-10-01",
            "ticker": "SHRT",
            "direction": "SHORT",
            "bucket": 1,
            "market_cap": 400_000_000,
            "ref_price": 50.0,
            "entry_low": 49.5,
            "entry_high": 50.5,
            "stop_price": 53.0,
            "target_price": 42.0,
            "reward_risk_ratio": 2.67,
            "conviction": 4,
        }
    )

    # Synthetic 25-day price series starting on 2026-10-01
    base = dt.date(2026, 10, 1)
    dates = [(base + dt.timedelta(days=i)).isoformat() for i in range(25)]

    def fake_fetcher(ticker: str, _start: str) -> list[dict]:
        bars = []
        for idx, d in enumerate(dates):
            if ticker == "WINR":
                px = 100.0 + idx * 1.0  # hits target 112.0 at day 12; day 20 = 120 (+20%)
                bars.append({"date": d, "high": px + 0.5, "low": px - 0.5, "close": px})
            elif ticker == "SHRT":
                px = 50.0 + idx * 0.5  # hits stop 53.0 at day 6
                bars.append({"date": d, "high": px + 0.2, "low": px - 0.2, "close": px})
            elif ticker == "SPY":
                px = 500.0 + idx * 1.25  # +5% by day 20
                bars.append({"date": d, "high": px, "low": px, "close": px})
            elif ticker == "IWM":
                px = 200.0 + idx * 0.2   # +2% by day 20
                bars.append({"date": d, "high": px, "low": px, "close": px})
        return bars

    graded = ledger.grade_ledger(fake_fetcher, as_of=dt.date(2026, 10, 26))
    assert graded == 2

    scorecard = ledger.get_30d_scorecard(as_of=dt.date(2026, 10, 26))
    b1 = scorecard["buckets"]["bucket_1"]
    assert b1["total_signals"] == 2
    assert b1["closed_signals"] == 2
    assert b1["win_pct"] == 50.0
    # Avg R = (2.4 + (-1.0)) / 2 = 0.70
    assert b1["avg_r_multiple"] == 0.7
