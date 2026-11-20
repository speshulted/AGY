"""Hermetic tests for deterministic report fact-validator and mobile digest notifier."""

from pathlib import Path

from src.notify.push import format_mobile_digest, send_notifications
from src.validate import validate_report_text

CANDIDATES = {
    "run_date": "2026-11-20",
    "buckets": {
        "bucket_1": [
            {
                "ticker": "ACME",
                "bucket": 1,
                "direction": "LONG",
                "market_cap": 250_000_000,
                "ref_price": 25.00,
                "rvol_20d": 2.8,
                "sec_urls": [],
                "warnings": [],
            }
        ],
        "bucket_2": [],
        "bucket_3": [],
    },
}

VALID_REPORT = """# Equity Pulse Daily Research Brief — 2026-11-20

### [LONG] ACME — Bucket 1 (1-5d)
- **Ref Price**: $25.00 | **RVOL**: 2.80x
- **Entry Zone**: $24.75 - $25.25 | **Stop Price**: $23.00 | **Target Price**: $30.00
- **How this fails (Invalidation)**: Break below $23.00 invalidates breakout structure.
"""


def test_valid_report_passes() -> None:
    res = validate_report_text(VALID_REPORT, CANDIDATES)
    assert res.ok is True
    assert res.cards_checked == 1
    assert res.parsed_signals[0]["reward_risk_ratio"] == 2.5


def test_validator_rejects_blocklisted_ticker_bad_rr_and_hallucinated_price() -> None:
    bad_report = """# Bad Brief

### [LONG] GOOGL — Bucket 1 (1-5d)
- **Ref Price**: $180.00
- **Entry Zone**: $179.00 - $181.00 | **Stop Price**: $175.00 | **Target Price**: $195.00
- **How this fails (Invalidation)**: Macro pullback.

### [LONG] ACME — Bucket 1 (1-5d)
- **Ref Price**: $99.00
- **Entry Zone**: $24.75 - $25.25 | **Stop Price**: $24.00 | **Target Price**: $26.00
"""
    res = validate_report_text(bad_report, CANDIDATES)
    assert res.ok is False
    joined = "\n".join(res.errors)
    assert "do_not_trade" in joined
    assert "deviates from candidates JSON" in joined
    assert "violates minimum 2.0" in joined
    assert "How this fails" in joined


def test_notify_dry_run_formats_digest(tmp_path: Path) -> None:
    rep = tmp_path / "2026-11-20.md"
    rep.write_text(VALID_REPORT, encoding="utf-8")
    digest = format_mobile_digest(VALID_REPORT)
    assert "[LONG] ACME" in digest

    out = send_notifications(rep, dry_run=True)
    assert out["dry_run"] is True
    assert "[LONG] ACME" in out["digest"]
