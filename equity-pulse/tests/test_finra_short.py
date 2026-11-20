"""Hermetic tests for FINRA Reg SHO daily short-volume and SEC FTD parsing."""

from src.ingest.finra_short import (
    parse_finra_regsho_text,
    parse_sec_ftd_text,
    summarize_short_trends,
)

SAMPLE_FINRA = """Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
20261119|ACME|600000|10000|1000000|B,Q,N
20261119|BETA|250000|0|1000000|Q
"""

SAMPLE_FINRA_OLD = """Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
20261101|ACME|350000|0|1000000|B,Q,N
"""

SAMPLE_FTD = """SETTLEMENT DATE|CUSIP|SYMBOL|QUANTITY (FAILS)|DESCRIPTION|PRICE
20261101|123456789|ACME|20000|ACME CORP|10.00
20261115|123456789|ACME|250000|ACME CORP|10.50
"""


def test_parse_finra_regsho_and_trend() -> None:
    day1 = parse_finra_regsho_text(SAMPLE_FINRA)
    day_old = parse_finra_regsho_text(SAMPLE_FINRA_OLD)
    assert day1["ACME"].short_vol_ratio == 0.6
    assert day1["BETA"].short_vol_ratio == 0.25

    ftds = parse_sec_ftd_text(SAMPLE_FTD)
    assert len(ftds["ACME"]) == 2
    assert ftds["ACME"][1].notional_usd == 2_625_000.0

    # 5 recent high-short days followed by 15 older lower-short days -> rising trend
    daily_maps = [day1] * 5 + [day_old] * 15
    summary = summarize_short_trends(daily_maps, "ACME", ftd_records=ftds["ACME"], float_shares=15_000_000)
    assert summary["latest_short_vol_ratio"] == 0.6
    assert summary["rising_short_trend"] is True
    assert summary["ftd_spike"] is True
    assert summary["short_or_ftd_spike"] is True
