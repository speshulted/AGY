"""Hermetic tests for the Equity Pulse graphic HTML web dashboard generator."""

import json
from pathlib import Path

from src.render_html import render_report_html, write_report_dashboard

SAMPLE_MD = """# Equity Pulse Daily Research Brief — 2026-11-20

## Plain-English Summary
- **Market Mood**: Broad small-cap trend is steady.

## Investing Recommendations (Long)

### [LONG] MSFT — Bucket 3 (1-6m+)
- **Plain-English Takeaway**: Strong free cash flow and 17.8% revenue growth in an uptrend.
- **Ref Price**: $484.52 | **RVOL**: 1.57x | **Market Cap**: $3,598,139,523,072
- **Entry Zone**: $469.98 - $499.06 | **Stop Price**: $426.38 | **Target Price**: $629.88 (R:R 2.50)
- **Deterministic Drivers**: Quality Growth Investing: TTM rev growth=17.8%
- **How this fails (Invalidation)**: Break below $426.38.

## Short Recommendations

### [SHORT] ABEO — Bucket 3 (1-6m+)
- **Plain-English Takeaway**: Accounting profits far ahead of cash flow with active S-3 shelf.
- **Ref Price**: $2.42 | **RVOL**: 1.86x | **Market Cap**: $165,305,088
- **Entry Zone**: $2.35 - $2.49 | **Stop Price**: $2.71 | **Target Price**: $1.69 (R:R 2.52)
- **Deterministic Drivers**: Accrual divergence 85.8%; Cash runway 3.5q
- **Risk Flags**: BABY-SHELF CAP (<$75M float)
- **Squeeze / Upside Hazard**: Monitor float liquidity.
"""

SAMPLE_CANDS = {
    "run_date": "2026-11-20",
    "macro": {"choppy_regime": False},
    "stats": {"total_loaded": 136, "passed_universe": 15, "rejected": {"GOOGL": "blocklisted_do_not_trade"}},
    "previous_day_accuracy": {
        "previous_run_date": "2026-11-19",
        "hit_rate_pct": 66.7,
        "profit_factor": 5.0,
        "avg_signed_return_pct": 2.67,
        "plain_english_summary": "2/3 prior calls moved in the predicted direction.",
        "items": [
            {
                "ticker": "GRWT",
                "direction": "LONG",
                "bucket": 3,
                "ref_price": 100.0,
                "latest_price": 104.0,
                "next_day_return_pct": 4.0,
                "verdict": "CORRECT",
                "outcome_status": "OPEN",
            }
        ],
    },
    "buckets": {
        "bucket_1": [],
        "bucket_2": [],
        "bucket_3": [
            {"ticker": "MSFT", "direction": "LONG", "score": 82.0, "market_cap": 3_598_139_523_072, "sec_urls": ["https://www.sec.gov/example"]},
            {"ticker": "ABEO", "direction": "SHORT", "score": 95.0, "market_cap": 165_305_088, "sec_urls": []},
        ],
    },
}


def test_render_html_includes_dual_strategy_svg_ladders_and_accuracy(tmp_path: Path) -> None:
    rep = tmp_path / "2026-11-20.md"
    cand = tmp_path / "candidates_2026-11-20.json"
    rep.write_text(SAMPLE_MD, encoding="utf-8")
    cand.write_text(json.dumps(SAMPLE_CANDS), encoding="utf-8")

    dated_html, index_html = write_report_dashboard(rep, cand, output_dir=tmp_path)
    assert dated_html.exists() and index_html.exists()
    content = dated_html.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content
    assert "INVESTING LONG" in content
    assert "MSFT" in content and "ABEO" in content
    assert "ladder-svg" in content and "chart-svg" in content
    assert "Prior-Day Recommendation Accuracy Review" in content
    assert "66.7%" in content and "CORRECT" in content
    assert "GOOGL" in content
