---
name: sec-forensics
description: >-
  Forensic guide for interpreting SEC Form 4 footnotes, Form 8-K item codes,
  Form S-3/424B5 shelf dilution mechanics, baby-shelf rules, and XBRL accrual red flags.
  Use when evaluating candidate SEC filings during Stage 2 or Stage 3 research.
---

# SEC Filing Forensics Guide

## 1. Form 4 Insider Transactions
- **High-signal buys**: Open-market purchase (`transactionCode == "P"`) by C-suite (CEO/CFO/COO) or Directors, **NOT** executed under a Rule 10b5-1 plan (`aff10b5One == 0` and no 10b5-1 footnote).
- **Noise to ignore**: `M` (option exercise), `A` (grant/award), `F` (tax withholding), `G` (gift).
- **Cluster rule**: `>= 2` distinct insiders totaling `>= $100,000` within 14 days strengthens conviction; `>= 3` officers selling (`S`) into technical weakness signals distribution.

## 2. Form 8-K Item Taxonomy
- **Positive / Constructive Catalysts**:
  - `1.01`: Entry into a Material Definitive Agreement (major customer contract, licensing, M&A)
  - `2.02`: Results of Operations and Financial Condition (earnings release / guidance raise)
  - `8.01`: Other Events (FDA updates, clinical readouts, buyback authorizations)
- **Negative / Red-Flag Catalysts**:
  - `1.02`: Termination of a Material Definitive Agreement
  - `2.06`: Material Impairments
  - `3.01`: Notice of Delisting or Failure to Satisfy a Continued Listing Rule
  - `4.02`: Non-Reliance on Previously Issued Financial Statements (restatement risk)

## 3. Form S-3 / 424B5 Dilution & Baby-Shelf Rules
- **Active Shelf Risk**: An S-3, S-3ASR, or 424B5 prospectus supplement within 90 days enables rapid At-The-Market (ATM) equity issuance into strength.
- **Baby-Shelf Rule (General Instruction I.B.6)**: If non-affiliated **public float < $75M**, the issuer is capped at selling **<= 1/3 of public float** in primary S-3 offerings over any trailing 12-month period. Above `$75M` float, S-3 dilution overhang is unconstrained.
- **Micro-Cap Long Safeguard (`$50M–$300M`)**: Always check 424B5 sales agent terms and remaining ATM capacity before recommending a breakout long.

## 4. XBRL Forensic Red Flags
- **Accrual Divergence**: `(Net Income - Operating Cash Flow) / Total Assets > +8%` indicates earnings outrunning cash collection (check DSO/receivables & inventory buildup).
- **Cash Runway**: `Cash / (|Annual OCF| / 4) < 4 quarters` paired with an active S-3/ATM indicates imminent equity dilution.
- **Altman Z-Score < 1.8**: Check 10-Q debt footnote for near-term maturities and covenant waivers.
