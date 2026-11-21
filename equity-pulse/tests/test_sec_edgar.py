"""Hermetic tests for SEC EDGAR Form 4 XML, 8-K classification, S-3 baby-shelf, and XBRL math."""

from src.ingest.sec_edgar import (
    TokenBucketRateLimiter,
    classify_8k_items,
    evaluate_baby_shelf,
    extract_xbrl_fundamentals,
    parse_form4_xml,
)

SAMPLE_FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <aff10b5One>0</aff10b5One>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerName>Jane Doe</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector>
      <isOfficer>1</isOfficer>
      <officerTitle>Chief Executive Officer</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-11-18</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>10000</value></transactionShares>
        <transactionPricePerShare><value>12.50</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-11-19</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>2000</value></transactionShares>
        <transactionPricePerShare><value>13.00</value></transactionPricePerShare>
      </transactionAmounts>
      <footnoteId id="F1"/>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
  <footnotes>
    <footnote id="F1">Sale executed pursuant to a Rule 10b5-1 trading plan adopted in 2025.</footnote>
  </footnotes>
</ownershipDocument>
"""


def test_parse_form4_xml_open_market_and_10b5_1() -> None:
    txs = parse_form4_xml(SAMPLE_FORM4_XML, filing_url="https://www.sec.gov/example")
    assert len(txs) == 2

    buy = txs[0]
    assert buy.insider_name == "Jane Doe"
    assert buy.is_officer and buy.is_director
    assert buy.transaction_code == "P"
    assert buy.value_usd == 125_000.0
    assert buy.is_10b5_1 is False

    sale = txs[1]
    assert sale.transaction_code == "S"
    assert sale.value_usd == 26_000.0
    assert sale.is_10b5_1 is True


def test_classify_8k_items() -> None:
    assert classify_8k_items(["1.01", "8.01"]) == "POSITIVE"
    assert classify_8k_items(["4.02"]) == "NEGATIVE"
    assert classify_8k_items(["2.02", "2.06"]) == "MIXED"
    assert classify_8k_items(["9.01"]) == "NEUTRAL"


def test_baby_shelf_rule() -> None:
    constrained, cap = evaluate_baby_shelf(public_float_usd=60_000_000, has_active_shelf=True)
    assert constrained is True
    assert cap == 20_000_000.0

    constrained_hi, cap_hi = evaluate_baby_shelf(public_float_usd=120_000_000, has_active_shelf=True)
    assert constrained_hi is False
    assert cap_hi is None


def test_extract_xbrl_fundamentals_accruals_and_runway() -> None:
    companyfacts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"form": "10-K", "fp": "FY", "end": "2024-12-31", "filed": "2025-02-20", "val": 100_000_000},
                            {"form": "10-K", "fp": "FY", "end": "2025-12-31", "filed": "2026-02-20", "val": 120_000_000},
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {"USD": [{"form": "10-K", "fp": "FY", "end": "2025-12-31", "filed": "2026-02-20", "val": 15_000_000}]}
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {"USD": [{"form": "10-K", "fp": "FY", "end": "2025-12-31", "filed": "2026-02-20", "val": -20_000_000}]}
                },
                "Assets": {
                    "units": {"USD": [{"form": "10-K", "fp": "FY", "end": "2025-12-31", "filed": "2026-02-20", "val": 200_000_000}]}
                },
                "CashAndCashEquivalentsAtCarryingValue": {
                    "units": {"USD": [{"form": "10-K", "fp": "FY", "end": "2025-12-31", "filed": "2026-02-20", "val": 15_000_000}]}
                },
            }
        }
    }
    f = extract_xbrl_fundamentals(companyfacts, market_cap=150_000_000)
    assert f.ttm_revenue_growth == 0.2
    # Accrual ratio: (15M - (-20M)) / 200M = 0.175 (17.5% > 8%)
    assert round(f.accrual_ratio or 0.0, 4) == 0.175
    # Quarterly burn = 20M / 4 = 5M -> 15M / 5M = 3.0 quarters (< 4q)
    assert f.cash_runway_quarters == 3.0


def test_rate_limiter_bounds() -> None:
    limiter = TokenBucketRateLimiter(rate_per_sec=8.0)
    limiter.acquire()
    assert limiter.rate == 8.0


def test_xbrl_derives_market_cap_and_ev_when_yahoo_missing() -> None:
    companyfacts = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"form": "10-Q", "end": "2026-09-30", "filed": "2026-11-01", "val": 10_000_000}
                        ]
                    }
                }
            },
            "us-gaap": {
                "LongTermDebt": {"units": {"USD": [{"form": "10-Q", "end": "2026-09-30", "filed": "2026-11-01", "val": 50_000_000}]}},
                "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [{"form": "10-Q", "end": "2026-09-30", "filed": "2026-11-01", "val": 20_000_000}]}},
            },
        }
    }
    f = extract_xbrl_fundamentals(companyfacts, market_cap=None, ref_price=25.0)
    assert f.shares_outstanding == 10_000_000
    assert f.derived_market_cap == 250_000_000.0
    assert f.derived_enterprise_value == 280_000_000.0
