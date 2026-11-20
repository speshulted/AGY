"""Deterministic Stage 3 Fact Validator for reports/YYYY-MM-DD.md."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config import UniverseConfig, load_universe_config

# Matches structured ticker card headers like: ### [LONG] NVDA — Bucket 1 (1-5d)
CARD_HEADER_RE = re.compile(
    r"^###\s+\[(LONG|SHORT)\]\s+([A-Z0-9.\-]+)\s+[—\-]+\s+Bucket\s+([123])",
    re.MULTILINE,
)

PRICE_LINE_RE = {
    "ref_price": re.compile(r"Ref(?:erence)?\s+Price\*{0,2}:\*{0,2}\s*\$?([0-9][0-9,]*\.?[0-9]*)", re.IGNORECASE),
    "entry_low": re.compile(r"Entry(?:\s+Zone)?\*{0,2}:\*{0,2}\s*\$?([0-9][0-9,]*\.?[0-9]*)", re.IGNORECASE),
    "entry_high": re.compile(r"Entry(?:\s+Zone)?\*{0,2}:\*{0,2}\s*\$?[0-9][0-9,]*\.?[0-9]*\s*[\-–to]+\s*\$?([0-9][0-9,]*\.?[0-9]*)", re.IGNORECASE),
    "stop_price": re.compile(r"Stop(?:\s+Price)?\*{0,2}:\*{0,2}\s*\$?([0-9][0-9,]*\.?[0-9]*)", re.IGNORECASE),
    "target_price": re.compile(r"Target(?:\s+Price)?\*{0,2}:\*{0,2}\s*\$?([0-9][0-9,]*\.?[0-9]*)", re.IGNORECASE),
    "rvol_20d": re.compile(r"RVOL(?:_20d)?\*{0,2}:\*{0,2}\s*([0-9]+\.?[0-9]*)x?", re.IGNORECASE),
}


@dataclass
class ValidationResult:
    ok: bool
    cards_checked: int = 0
    errors: list[str] = field(default_factory=list)
    parsed_signals: list[dict[str, Any]] = field(default_factory=list)


def _to_float(match: re.Match[str] | None) -> float | None:
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _within_tolerance(reported: float, truth: float, tol_pct: float) -> bool:
    if truth == 0:
        return abs(reported) <= 1e-6
    return abs(reported - truth) / abs(truth) <= tol_pct


def validate_report_text(
    report_text: str,
    candidates_payload: dict[str, Any],
    cfg: UniverseConfig | None = None,
) -> ValidationResult:
    """Validate ticker cards in a daily report against candidates JSON & standing invariants."""
    config = cfg or load_universe_config()
    errors: list[str] = []
    parsed_signals: list[dict[str, Any]] = []

    # Build lookup of candidate truth by (ticker, direction, bucket) and by ticker
    cand_by_key: dict[tuple[str, str, int], dict[str, Any]] = {}
    cand_by_ticker: dict[str, dict[str, Any]] = {}
    for bucket_key in ("bucket_1", "bucket_2", "bucket_3"):
        for item in candidates_payload.get("buckets", {}).get(bucket_key, []):
            t = str(item["ticker"]).upper()
            d = str(item["direction"]).upper()
            b = int(item["bucket"])
            cand_by_key[(t, d, b)] = item
            cand_by_ticker[t] = item

    # Hard check: no blocked ticker mentioned as a recommended card anywhere in the report
    matches = list(CARD_HEADER_RE.finditer(report_text))
    for i, m in enumerate(matches):
        direction = m.group(1).upper()
        ticker = m.group(2).upper()
        bucket = int(m.group(3))
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(report_text)
        block = report_text[start:end]

        if ticker in config.do_not_trade:
            errors.append(f"{ticker}: present in do_not_trade blocklist.")
            continue

        truth = cand_by_key.get((ticker, direction, bucket)) or cand_by_ticker.get(ticker)
        if truth is None:
            errors.append(f"{ticker} ({direction}, Bucket {bucket}): not found in candidates JSON.")
            continue

        ref_p = _to_float(PRICE_LINE_RE["ref_price"].search(block))
        entry_lo = _to_float(PRICE_LINE_RE["entry_low"].search(block))
        entry_hi = _to_float(PRICE_LINE_RE["entry_high"].search(block)) or entry_lo
        stop_p = _to_float(PRICE_LINE_RE["stop_price"].search(block))
        target_p = _to_float(PRICE_LINE_RE["target_price"].search(block))
        rvol_p = _to_float(PRICE_LINE_RE["rvol_20d"].search(block))

        # 1. Verify numeric facts match candidates JSON
        if ref_p is None:
            errors.append(f"{ticker}: missing 'Ref Price'.")
        elif not _within_tolerance(ref_p, float(truth["ref_price"]), config.numeric_tolerance_pct):
            errors.append(
                f"{ticker}: Ref Price ${ref_p:.2f} deviates from candidates JSON ${float(truth['ref_price']):.2f}."
            )

        if rvol_p is not None and truth.get("rvol_20d") is not None:
            if not _within_tolerance(rvol_p, float(truth["rvol_20d"]), max(config.numeric_tolerance_pct, 0.05)):
                errors.append(
                    f"{ticker}: RVOL {rvol_p:.2f}x deviates from candidates JSON {float(truth['rvol_20d']):.2f}x."
                )

        # 2. Verify stop/target orientation and R:R >= min_reward_risk_ratio (2.0)
        if entry_lo is None or entry_hi is None or stop_p is None or target_p is None:
            errors.append(f"{ticker}: missing Entry / Stop / Target price levels.")
        else:
            entry_mid = (entry_lo + entry_hi) / 2.0
            if direction == "LONG":
                risk = entry_mid - stop_p
                reward = target_p - entry_mid
            else:
                risk = stop_p - entry_mid
                reward = entry_mid - target_p

            if risk <= 0 or reward <= 0:
                errors.append(f"{ticker} ({direction}): invalid stop (${stop_p}) / target (${target_p}) vs entry (${entry_mid:.2f}).")
            else:
                rr = reward / risk
                if rr + 1e-6 < config.min_reward_risk_ratio:
                    errors.append(
                        f"{ticker} ({direction}): R:R {rr:.2f} violates minimum {config.min_reward_risk_ratio:.1f}."
                    )
                else:
                    parsed_signals.append(
                        {
                            "ticker": ticker,
                            "direction": direction,
                            "bucket": bucket,
                            "market_cap": truth.get("market_cap"),
                            "ref_price": ref_p or float(truth["ref_price"]),
                            "entry_low": entry_lo,
                            "entry_high": entry_hi,
                            "stop_price": stop_p,
                            "target_price": target_p,
                            "reward_risk_ratio": round(rr, 2),
                            "sec_urls": truth.get("sec_urls", []),
                            "critic_warnings": "; ".join(truth.get("warnings", [])),
                        }
                    )

        # 3. Verify mandatory counter-thesis section
        if direction == "LONG":
            if not re.search(r"how this fails|invalidation", block, re.IGNORECASE):
                errors.append(f"{ticker} (LONG): missing mandatory 'How this fails (Invalidation)' counter-thesis.")
        else:
            if not re.search(r"squeeze|upside hazard", block, re.IGNORECASE):
                errors.append(f"{ticker} (SHORT): missing mandatory 'Squeeze / Upside Hazard' counter-thesis.")

    return ValidationResult(
        ok=len(errors) == 0,
        cards_checked=len(matches),
        errors=errors,
        parsed_signals=parsed_signals,
    )


def validate_report(
    report_path: Path,
    candidates_path: Path,
    cfg: UniverseConfig | None = None,
) -> ValidationResult:
    """Validate a report Markdown file against a candidates JSON file."""
    report_text = Path(report_path).read_text(encoding="utf-8")
    candidates_payload = json.loads(Path(candidates_path).read_text(encoding="utf-8"))
    return validate_report_text(report_text, candidates_payload, cfg=cfg)
