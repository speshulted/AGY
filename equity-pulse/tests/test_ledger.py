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


def test_previous_day_accuracy_analysis(tmp_path: Path) -> None:
    ledger = SignalLedger(tmp_path / "prev_day.db")
    # Yesterday (2026-11-19): 1 Investing LONG (right) + 1 SHORT (right) + 1 SHORT (wrong)
    ledger.record_signal(
        {
            "run_date": "2026-11-19",
            "ticker": "GRWT",
            "direction": "LONG",
            "bucket": 3,
            "ref_price": 100.0,
            "entry_low": 97.0,
            "entry_high": 103.0,
            "stop_price": 88.0,
            "target_price": 130.0,
            "reward_risk_ratio": 2.5,
        }
    )
    ledger.record_signal(
        {
            "run_date": "2026-11-19",
            "ticker": "BEAR",
            "direction": "SHORT",
            "bucket": 3,
            "ref_price": 50.0,
            "entry_low": 48.5,
            "entry_high": 51.5,
            "stop_price": 56.0,
            "target_price": 35.0,
            "reward_risk_ratio": 2.5,
        }
    )
    ledger.record_signal(
        {
            "run_date": "2026-11-19",
            "ticker": "MISS",
            "direction": "SHORT",
            "bucket": 1,
            "ref_price": 20.0,
            "entry_low": 19.8,
            "entry_high": 20.2,
            "stop_price": 22.0,
            "target_price": 15.0,
            "reward_risk_ratio": 2.5,
        }
    )

    def next_day_fetcher(ticker: str, _start: str) -> list[dict]:
        moves = {
            "GRWT": [{"date": "2026-11-20", "high": 105.0, "low": 101.0, "close": 104.0}],  # +4% LONG -> CORRECT
            "BEAR": [{"date": "2026-11-20", "high": 49.5, "low": 46.0, "close": 47.0}],    # +6% SHORT -> CORRECT
            "MISS": [{"date": "2026-11-20", "high": 21.2, "low": 20.1, "close": 20.4}],    # -2% SHORT -> INCORRECT
            "SPY": [{"date": "2026-11-20", "high": 500.0, "low": 498.0, "close": 499.0}],
            "IWM": [{"date": "2026-11-20", "high": 257.0, "low": 255.0, "close": 256.0}],
        }
        return moves.get(ticker, [])

    ledger.grade_ledger(next_day_fetcher, as_of=dt.date(2026, 11, 20))
    acc = ledger.get_previous_day_accuracy(as_of=dt.date(2026, 11, 20))

    assert acc["previous_run_date"] == "2026-11-19"
    assert acc["total_recommendations"] == 3
    assert acc["evaluated_count"] == 3
    assert acc["hits"] == 2
    assert acc["hit_rate_pct"] == 66.7
    # Gross gains = 0.04 + 0.06 = 0.10; Gross losses = 0.02 -> Profit Factor = 5.0
    assert acc["profit_factor"] == 5.0
    assert acc["long_accuracy"]["hit_rate_pct"] == 100.0
    assert acc["short_accuracy"]["hit_rate_pct"] == 50.0
    by_ticker = {x["ticker"]: x for x in acc["items"]}
    assert by_ticker["GRWT"]["verdict"] == "CORRECT"
    assert by_ticker["BEAR"]["verdict"] == "CORRECT"
    assert by_ticker["MISS"]["verdict"] == "INCORRECT"
