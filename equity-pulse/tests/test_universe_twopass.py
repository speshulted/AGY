"""Hermetic tests for Full US Equity Universe discovery and Two-Pass Long/Short pre-screening."""

from src.config import load_universe_config
from src.ingest.finra_short import FinraShortRecord
from src.ingest.universe import (
    build_us_equity_universe,
    parse_nasdaq_listed_text,
    parse_other_listed_text,
    select_pass2_candidates,
)

SAMPLE_NASDAQ = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
GOOGL|Alphabet Inc. - Class A Common Stock|Q|N|N|100|N|N
QQQ|Invesco QQQ Trust|G|N|N|100|Y|N
ACMEW|Acme Corp Warrants|S|N|N|100|N|N
TESTX|Test Equity|Q|Y|N|100|N|N
NOSEC|No SEC Filer Corp - Common Stock|Q|N|N|100|N|N
File Creation Time: 1120202618:00||||||
"""

SAMPLE_OTHER = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
JPM|JPMorgan Chase & Co. Common Stock|N|JPM|N|100|N|JPM
SENS|Senseonics Holdings, Inc. Common Stock|A|SENS|N|100|N|SENS
SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY
BRK.B|Berkshire Hathaway Class B|N|BRK.B|N|100|N|BRK.B
SPACU|Example Acquisition Corp Units|N|SPACU|N|100|N|SPACU
File Creation Time: 1120202618:00||||||
"""


def test_universe_discovery_filters_etfs_warrants_spacs_and_blocklist() -> None:
    cfg = load_universe_config()
    nas = parse_nasdaq_listed_text(SAMPLE_NASDAQ)
    assert set(nas.keys()) == {"AAPL", "GOOGL", "NOSEC"}

    oth = parse_other_listed_text(SAMPLE_OTHER)
    assert set(oth.keys()) == {"JPM", "SENS"}

    sec_ciks = {"AAPL": 320193, "GOOGL": 1652044, "JPM": 19617, "SENS": 1616543}
    uni = build_us_equity_universe(SAMPLE_NASDAQ, SAMPLE_OTHER, sec_ciks, cfg)
    assert set(uni.keys()) == {"AAPL", "JPM", "SENS"}
    assert "GOOGL" not in uni  # blocklist enforced
    assert "NOSEC" not in uni  # SEC CIK intersection enforced


def test_pass2_selector_balances_long_and_short_triggers() -> None:
    cfg = load_universe_config()
    snapshots = {
        # Bucket 1 Long breakout
        "BRKT": {
            "close": 50.0,
            "sma50": 44.0,
            "sma200": 38.0,
            "ema20": 46.0,
            "high_20d": 49.0,
            "high_55d": 49.5,
            "rvol_20d": 2.6,
            "avg_dollar_vol_20d": 25_000_000.0,
            "atr_extension": 1.4,
        },
        # Bucket 3 Long value base near 52w low
        "VALU": {
            "close": 22.0,
            "low_52w": 20.0,
            "stabilizing_rs": True,
            "rvol_20d": 1.1,
            "avg_dollar_vol_20d": 5_000_000.0,
        },
        # Bucket 1 Short breakdown
        "BRKD": {
            "close": 18.0,
            "sma50": 23.0,
            "sma200": 26.0,
            "low_20d": 19.0,
            "rvol_20d": 2.9,
            "avg_dollar_vol_20d": 12_000_000.0,
            "has_lower_high_under_sma50": True,
            "stabilizing_rs": False,
        },
        # Illiquid penny stock (must be filtered out)
        "PENY": {
            "close": 1.25,
            "sma50": 1.0,
            "sma200": 0.9,
            "high_20d": 1.1,
            "rvol_20d": 5.0,
            "avg_dollar_vol_20d": 2_000_000.0,
        },
    }
    finra_today = {
        "BRKD": FinraShortRecord("20261119", "BRKD", 600_000, 0, 1_000_000, 0.60),
    }
    finra_old = {
        "BRKD": FinraShortRecord("20261101", "BRKD", 350_000, 0, 1_000_000, 0.35),
    }
    selected = select_pass2_candidates(snapshots, [finra_today] * 5 + [finra_old] * 15, cfg, max_enrich=10)
    tickers = {x["ticker"]: x for x in selected}

    assert "BRKT" in tickers and tickers["BRKT"]["side"] == "LONG"
    assert "VALU" in tickers and tickers["VALU"]["side"] == "LONG"
    assert "BRKD" in tickers and tickers["BRKD"]["side"] == "SHORT"
    assert "PENY" not in tickers
