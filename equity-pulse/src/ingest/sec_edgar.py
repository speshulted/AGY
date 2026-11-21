"""SEC EDGAR REST API client, rate limiter, Form 4 / 8-K / S-3 / XBRL parsers."""

from __future__ import annotations

import datetime as dt
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from typing import Any

import requests

from src.config import get_sec_user_agent

POSITIVE_8K_ITEMS = {"1.01", "2.02", "8.01"}
NEGATIVE_8K_ITEMS = {"1.02", "2.06", "3.01", "4.02"}
DILUTION_FORMS = {"S-3", "S-3ASR", "S-3/A", "424B5", "F-3", "F-3ASR"}


class TokenBucketRateLimiter:
    """Thread-safe token bucket enforcing SEC EDGAR <= 8 requests/sec fair-access policy."""

    def __init__(self, rate_per_sec: float = 8.0, capacity: float | None = None) -> None:
        if rate_per_sec <= 0 or rate_per_sec > 10.0:
            raise ValueError("SEC EDGAR rate_per_sec must be in (0, 10].")
        self.rate = float(rate_per_sec)
        self.capacity = float(capacity if capacity is not None else max(1.0, rate_per_sec))
        self._tokens = self.capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                self._last_refill = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                wait_seconds = (tokens - self._tokens) / self.rate
            time.sleep(max(wait_seconds, 0.005))


@dataclass
class Form4Transaction:
    insider_name: str
    insider_title: str
    is_director: bool
    is_officer: bool
    transaction_date: str
    transaction_code: str  # 'P' open-market purchase, 'S' sale, etc.
    shares: float
    price: float
    value_usd: float
    is_10b5_1: bool
    filing_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EightKEvent:
    filing_date: str
    items: list[str]
    sentiment: str  # "POSITIVE" | "NEGATIVE" | "MIXED" | "NEUTRAL"
    filing_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DilutionShelfStatus:
    has_active_shelf_90d: bool = False
    recent_forms: list[str] = field(default_factory=list)
    most_recent_date: str | None = None
    filing_urls: list[str] = field(default_factory=list)
    baby_shelf_constrained: bool = False
    max_baby_shelf_capacity_usd: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class XbrlFundamentals:
    ttm_revenue: float | None = None
    prior_ttm_revenue: float | None = None
    ttm_revenue_growth: float | None = None
    qoq_revenue_growth: float | None = None
    operating_cash_flow: float | None = None
    capex: float | None = None
    free_cash_flow: float | None = None
    net_income: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    retained_earnings: float | None = None
    ebit: float | None = None
    ebitda: float | None = None
    cash_and_equivalents: float | None = None
    total_debt: float | None = None
    net_debt: float | None = None
    shares_outstanding: float | None = None
    derived_market_cap: float | None = None
    derived_enterprise_value: float | None = None
    accrual_ratio: float | None = None        # (Net Income - OCF) / Total Assets
    cash_runway_quarters: float | None = None  # Cash / quarterly burn if OCF < 0
    altman_z: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _text(node: ET.Element | None, path: str, default: str = "") -> str:
    if node is None:
        return default
    el = node.find(path)
    if el is None or el.text is None:
        return default
    return el.text.strip()


def _float_val(node: ET.Element | None, path: str) -> float:
    raw = _text(node, path, "")
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return 0.0


def parse_form4_xml(xml_text: str, filing_url: str = "") -> list[Form4Transaction]:
    """Parse an SEC Form 4 XML document into structured open-market transactions."""
    root = ET.fromstring(xml_text)

    footnotes: dict[str, str] = {}
    for fn in root.findall(".//footnotes/footnote"):
        fid = fn.attrib.get("id", "")
        footnotes[fid] = "".join(fn.itertext()).strip()

    # Track which footnote IDs are explicitly referenced by any transaction
    referenced_fn_ids = {
        f.attrib.get("id", "")
        for f in root.findall(".//nonDerivativeTable/nonDerivativeTransaction//footnoteId")
    }
    unreferenced_fn_text = " ".join(
        text for fid, text in footnotes.items() if fid not in referenced_fn_ids
    ).lower()
    doc_has_unscoped_10b5_1 = bool(re.search(r"10b5-1|rule\s+10b5", unreferenced_fn_text))

    owner_name = _text(root, ".//reportingOwner/reportingOwnerId/rptOwnerName", "Unknown")
    rel = root.find(".//reportingOwner/reportingOwnerRelationship")
    is_director = _text(rel, "isDirector") in {"1", "true", "True"}
    is_officer = _text(rel, "isOfficer") in {"1", "true", "True"}
    title = _text(rel, "officerTitle", "Director" if is_director else "")

    txs: list[Form4Transaction] = []
    for tx in root.findall(".//nonDerivativeTable/nonDerivativeTransaction"):
        tx_date = _text(tx, "transactionDate/value")
        code = _text(tx, "transactionCoding/transactionCode").upper()
        shares = _float_val(tx, "transactionAmounts/transactionShares/value")
        price = _float_val(tx, "transactionAmounts/transactionPricePerShare/value")

        # Check transaction-specific footnote references for 10b5-1
        fn_ids = [f.attrib.get("id", "") for f in tx.findall(".//footnoteId")]
        tx_fn_text = " ".join(footnotes.get(fid, "") for fid in fn_ids).lower()
        is_10b5_1 = bool(re.search(r"10b5-1|rule\s+10b5", tx_fn_text)) or doc_has_unscoped_10b5_1

        # Also respect SEC Form 4 XML <aff10b5One> flag (introduced 2023)
        if _text(root, ".//aff10b5One") in {"1", "true", "True"}:
            is_10b5_1 = True

        txs.append(
            Form4Transaction(
                insider_name=owner_name,
                insider_title=title,
                is_director=is_director,
                is_officer=is_officer,
                transaction_date=tx_date,
                transaction_code=code,
                shares=shares,
                price=price,
                value_usd=round(shares * price, 2),
                is_10b5_1=is_10b5_1,
                filing_url=filing_url,
            )
        )
    return txs


def classify_8k_items(items: list[str]) -> str:
    """Classify 8-K item codes into POSITIVE / NEGATIVE / MIXED / NEUTRAL."""
    normalized = {str(i).strip() for i in items if str(i).strip()}
    has_pos = bool(normalized & POSITIVE_8K_ITEMS)
    has_neg = bool(normalized & NEGATIVE_8K_ITEMS)
    if has_pos and has_neg:
        return "MIXED"
    if has_neg:
        return "NEGATIVE"
    if has_pos:
        return "POSITIVE"
    return "NEUTRAL"


def evaluate_baby_shelf(
    public_float_usd: float | None,
    has_active_shelf: bool,
    baby_shelf_threshold: float = 75_000_000.0,
) -> tuple[bool, float | None]:
    """Evaluate SEC Form S-3 baby-shelf rule (General Instruction I.B.6).

    Issuers with public float < $75M are capped at selling <= 1/3 of public float
    in primary S-3 offerings over any trailing 12-month period.
    """
    if not has_active_shelf or public_float_usd is None or public_float_usd <= 0:
        return False, None
    if public_float_usd < baby_shelf_threshold:
        return True, round(public_float_usd / 3.0, 2)
    return False, None


def _latest_fact_series(
    us_gaap: dict[str, Any],
    tag_candidates: list[str],
    unit: str = "USD",
    forms: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Extract sorted fact entries for the first matching XBRL tag."""
    allowed_forms = forms or {"10-K", "10-Q"}
    for tag in tag_candidates:
        node = us_gaap.get(tag)
        if not node:
            continue
        entries = node.get("units", {}).get(unit, [])
        filtered = [
            e for e in entries
            if e.get("form") in allowed_forms and e.get("val") is not None and e.get("end")
        ]
        if filtered:
            filtered.sort(key=lambda e: (e.get("end", ""), e.get("filed", "")))
            return filtered
    return []


def _latest_annual_or_quarterly(entries: list[dict[str, Any]], annual_only: bool = False) -> float | None:
    if not entries:
        return None
    if annual_only:
        fy = [e for e in entries if e.get("form") == "10-K" or e.get("fp") == "FY"]
        if fy:
            return float(fy[-1]["val"])
    return float(entries[-1]["val"])


def extract_xbrl_fundamentals(
    companyfacts: dict[str, Any],
    market_cap: float | None = None,
    ref_price: float | None = None,
) -> XbrlFundamentals:
    """Compute key fundamental, accrual, runway, and Altman Z metrics from XBRL companyfacts."""
    facts = companyfacts.get("facts", {})
    us_gaap = facts.get("us-gaap", {})
    dei = facts.get("dei", {})

    shares_entries = _latest_fact_series(
        dei, ["EntityCommonStockSharesOutstanding"], unit="shares"
    ) or _latest_fact_series(
        us_gaap,
        ["CommonStockSharesOutstanding", "WeightedAverageNumberOfDilutedSharesOutstanding"],
        unit="shares",
    )
    shares_outstanding = _latest_annual_or_quarterly(shares_entries)
    derived_mcap = (
        round(shares_outstanding * ref_price, 2)
        if (shares_outstanding is not None and shares_outstanding > 0 and ref_price is not None and ref_price > 0)
        else None
    )
    effective_mcap = market_cap if (market_cap is not None and market_cap > 0) else derived_mcap

    rev_entries = _latest_fact_series(
        us_gaap,
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        ],
    )
    ocf_entries = _latest_fact_series(us_gaap, ["NetCashProvidedByUsedInOperatingActivities"])
    capex_entries = _latest_fact_series(
        us_gaap,
        [
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "CapitalExpendituresIncurredButNotYetPaid",
        ],
    )
    ni_entries = _latest_fact_series(us_gaap, ["NetIncomeLoss", "ProfitLoss"])
    assets_entries = _latest_fact_series(us_gaap, ["Assets"])
    liab_entries = _latest_fact_series(us_gaap, ["Liabilities"])
    curr_assets_entries = _latest_fact_series(us_gaap, ["AssetsCurrent"])
    curr_liab_entries = _latest_fact_series(us_gaap, ["LiabilitiesCurrent"])
    ret_earn_entries = _latest_fact_series(us_gaap, ["RetainedEarningsAccumulatedDeficit"])
    ebit_entries = _latest_fact_series(us_gaap, ["OperatingIncomeLoss"])
    da_entries = _latest_fact_series(
        us_gaap,
        ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization"],
    )
    cash_entries = _latest_fact_series(
        us_gaap,
        [
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsAndShortTermInvestments",
        ],
    )
    debt_long = _latest_fact_series(us_gaap, ["LongTermDebtNoncurrent", "LongTermDebt"])
    debt_short = _latest_fact_series(us_gaap, ["DebtCurrent", "ShortTermBorrowings", "LongTermDebtCurrent"])

    # Annual (10-K / FY) revenues for YoY/TTM growth, plus quarterly for QoQ
    fy_revs = [e for e in rev_entries if e.get("form") == "10-K" or e.get("fp") == "FY"]
    q_revs = [e for e in rev_entries if e.get("form") == "10-Q" and e.get("fp") in {"Q1", "Q2", "Q3", "Q4"}]

    ttm_rev = float(fy_revs[-1]["val"]) if fy_revs else _latest_annual_or_quarterly(rev_entries)
    prior_ttm_rev = float(fy_revs[-2]["val"]) if len(fy_revs) >= 2 else None
    ttm_rev_growth = (
        (ttm_rev - prior_ttm_rev) / abs(prior_ttm_rev)
        if ttm_rev is not None and prior_ttm_rev not in (None, 0)
        else None
    )
    qoq_rev_growth = None
    if len(q_revs) >= 2 and q_revs[-2]["val"] not in (None, 0):
        qoq_rev_growth = (float(q_revs[-1]["val"]) - float(q_revs[-2]["val"])) / abs(float(q_revs[-2]["val"]))

    ocf = _latest_annual_or_quarterly(ocf_entries, annual_only=True)
    capex = _latest_annual_or_quarterly(capex_entries, annual_only=True)
    fcf = (ocf - abs(capex)) if (ocf is not None and capex is not None) else ocf

    net_income = _latest_annual_or_quarterly(ni_entries, annual_only=True)
    total_assets = _latest_annual_or_quarterly(assets_entries)
    total_liabilities = _latest_annual_or_quarterly(liab_entries)
    current_assets = _latest_annual_or_quarterly(curr_assets_entries)
    current_liabilities = _latest_annual_or_quarterly(curr_liab_entries)
    retained_earnings = _latest_annual_or_quarterly(ret_earn_entries)
    ebit = _latest_annual_or_quarterly(ebit_entries, annual_only=True)
    da = _latest_annual_or_quarterly(da_entries, annual_only=True)
    ebitda = (ebit + abs(da)) if (ebit is not None and da is not None) else ebit
    cash = _latest_annual_or_quarterly(cash_entries)

    lt_debt_val = _latest_annual_or_quarterly(debt_long) or 0.0
    st_debt_val = _latest_annual_or_quarterly(debt_short) or 0.0
    total_debt = (lt_debt_val + st_debt_val) if (debt_long or debt_short) else None
    net_debt = (total_debt - cash) if (total_debt is not None and cash is not None) else None
    derived_ev = (
        round(effective_mcap + (net_debt or 0.0), 2)
        if effective_mcap is not None
        else None
    )

    accrual_ratio = None
    if net_income is not None and ocf is not None and total_assets not in (None, 0):
        accrual_ratio = (net_income - ocf) / total_assets

    cash_runway_quarters = None
    if cash is not None and ocf is not None and ocf < 0:
        quarterly_burn = abs(ocf) / 4.0
        if quarterly_burn > 0:
            cash_runway_quarters = cash / quarterly_burn

    altman_z = None
    if (
        total_assets not in (None, 0)
        and total_liabilities not in (None, 0)
        and current_assets is not None
        and current_liabilities is not None
        and retained_earnings is not None
        and ebit is not None
        and ttm_rev is not None
        and effective_mcap is not None
    ):
        wc = current_assets - current_liabilities
        altman_z = (
            1.2 * (wc / total_assets)
            + 1.4 * (retained_earnings / total_assets)
            + 3.3 * (ebit / total_assets)
            + 0.6 * (effective_mcap / total_liabilities)
            + 1.0 * (ttm_rev / total_assets)
        )

    return XbrlFundamentals(
        ttm_revenue=ttm_rev,
        prior_ttm_revenue=prior_ttm_rev,
        ttm_revenue_growth=ttm_rev_growth,
        qoq_revenue_growth=qoq_rev_growth,
        operating_cash_flow=ocf,
        capex=capex,
        free_cash_flow=fcf,
        net_income=net_income,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        current_assets=current_assets,
        current_liabilities=current_liabilities,
        retained_earnings=retained_earnings,
        ebit=ebit,
        ebitda=ebitda,
        cash_and_equivalents=cash,
        total_debt=total_debt,
        net_debt=net_debt,
        shares_outstanding=shares_outstanding,
        derived_market_cap=derived_mcap,
        derived_enterprise_value=derived_ev,
        accrual_ratio=accrual_ratio,
        cash_runway_quarters=cash_runway_quarters,
        altman_z=altman_z,
    )


class SecEdgarClient:
    """Rate-limited SEC EDGAR REST client (<= 8 req/sec)."""

    TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
    COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

    def __init__(
        self,
        user_agent: str | None = None,
        rate_per_sec: float = 8.0,
        timeout: float = 15.0,
    ) -> None:
        self.user_agent = user_agent or get_sec_user_agent()
        self.limiter = TokenBucketRateLimiter(rate_per_sec=rate_per_sec)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"})
        self._cik_map: dict[str, int] | None = None

    def _get(self, url: str) -> requests.Response:
        self.limiter.acquire()
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp

    def get_ticker_cik_map(self) -> dict[str, int]:
        if self._cik_map is None:
            data = self._get(self.TICKERS_URL).json()
            self._cik_map = {
                str(entry["ticker"]).upper(): int(entry["cik_str"])
                for entry in data.values()
                if "ticker" in entry and "cik_str" in entry
            }
        return self._cik_map

    def fetch_submissions(self, cik: int) -> dict[str, Any]:
        return self._get(self.SUBMISSIONS_URL.format(cik=cik)).json()

    def fetch_companyfacts(self, cik: int) -> dict[str, Any]:
        return self._get(self.COMPANYFACTS_URL.format(cik=cik)).json()

    def fetch_ticker_sec_profile(
        self,
        ticker: str,
        as_of: dt.date,
        market_cap: float | None = None,
        ref_price: float | None = None,
        public_float_usd: float | None = None,
        dilution_lookback_days: int = 90,
        max_form4_xmls: int = 6,
    ) -> dict[str, Any]:
        """Collect Form 4, 8-K, S-3/424B5 dilution status, and XBRL fundamentals for a ticker."""
        cik_map = self.get_ticker_cik_map()
        cik = cik_map.get(ticker.upper())
        if cik is None:
            return {
                "cik": None,
                "has_sec_filings": False,
                "has_recent_24h_filing": False,
                "form4_transactions": [],
                "eight_k_events": [],
                "dilution": DilutionShelfStatus().to_dict(),
                "fundamentals": XbrlFundamentals().to_dict(),
                "sec_urls": [],
            }

        sub = self.fetch_submissions(cik)
        recent = sub.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])
        items_list = recent.get("items", [])

        eight_k_events: list[EightKEvent] = []
        form4_txs: list[Form4Transaction] = []
        dilution_forms_found: list[str] = []
        dilution_urls: list[str] = []
        most_recent_dilution: str | None = None
        sec_urls: list[str] = []
        has_recent_24h = False

        cutoff_14d = as_of - dt.timedelta(days=14)
        cutoff_90d = as_of - dt.timedelta(days=dilution_lookback_days)
        cutoff_24h = as_of - dt.timedelta(days=1)

        form4_fetched = 0
        for idx, form in enumerate(forms):
            fdate_str = dates[idx] if idx < len(dates) else ""
            try:
                fdate = dt.date.fromisoformat(fdate_str)
            except ValueError:
                continue

            acc = accessions[idx] if idx < len(accessions) else ""
            acc_nodash = acc.replace("-", "")
            pdoc = primary_docs[idx] if idx < len(primary_docs) else ""
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{pdoc}" if acc_nodash and pdoc else ""

            if form in {"8-K", "8-K/A"} and fdate >= cutoff_14d:
                raw_items = items_list[idx] if idx < len(items_list) else []
                if isinstance(raw_items, str):
                    raw_items = [x.strip() for x in raw_items.split(",") if x.strip()]
                ev = EightKEvent(
                    filing_date=fdate_str,
                    items=list(raw_items),
                    sentiment=classify_8k_items(list(raw_items)),
                    filing_url=url,
                )
                eight_k_events.append(ev)
                if url:
                    sec_urls.append(url)
                if fdate >= cutoff_24h:
                    has_recent_24h = True

            elif form == "4" and fdate >= cutoff_14d:
                if fdate >= cutoff_24h:
                    has_recent_24h = True
                if form4_fetched < max_form4_xmls and url:
                    # Strip XSLT prefix (e.g. xslF345X05/form4.xml -> form4.xml) to fetch raw XML
                    raw_doc = pdoc.split("/")[-1]
                    raw_xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{raw_doc}"
                    try:
                        xml_resp = self._get(raw_xml_url)
                        parsed = parse_form4_xml(xml_resp.text, filing_url=url)
                        form4_txs.extend(parsed)
                        sec_urls.append(url)
                        form4_fetched += 1
                    except Exception:
                        pass

            elif form.upper() in DILUTION_FORMS and fdate >= cutoff_90d:
                dilution_forms_found.append(form.upper())
                if most_recent_dilution is None or fdate_str > most_recent_dilution:
                    most_recent_dilution = fdate_str
                if url:
                    dilution_urls.append(url)
                    sec_urls.append(url)

        has_shelf = bool(dilution_forms_found)
        baby_constrained, baby_cap = evaluate_baby_shelf(public_float_usd, has_shelf)
        dilution = DilutionShelfStatus(
            has_active_shelf_90d=has_shelf,
            recent_forms=sorted(set(dilution_forms_found)),
            most_recent_date=most_recent_dilution,
            filing_urls=dilution_urls[:5],
            baby_shelf_constrained=baby_constrained,
            max_baby_shelf_capacity_usd=baby_cap,
        )

        fundamentals = XbrlFundamentals()
        try:
            facts = self.fetch_companyfacts(cik)
            fundamentals = extract_xbrl_fundamentals(facts, market_cap=market_cap, ref_price=ref_price)
        except Exception:
            pass

        return {
            "cik": cik,
            "has_sec_filings": True,
            "has_recent_24h_filing": has_recent_24h,
            "form4_transactions": [t.to_dict() for t in form4_txs],
            "eight_k_events": [e.to_dict() for e in eight_k_events],
            "dilution": dilution.to_dict(),
            "fundamentals": fundamentals.to_dict(),
            "sec_urls": list(dict.fromkeys(sec_urls))[:10],
        }
