"""Equity Pulse CLI: ingest, grade-ledger, screen, validate-report, record-ledger, notify."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from src.config import DATA_DIR, REPORTS_DIR, load_env, load_universe_config
from src.ingest.finra_short import fetch_recent_finra_regsho, summarize_short_trends
from src.ingest.macro_earnings import fetch_finnhub_earnings_signal, fetch_macro_context
from src.ingest.market_data import MarketCacheStore, fetch_batch_market_snapshots
from src.ingest.sec_edgar import SecEdgarClient
from src.ledger.store import SignalLedger
from src.notify.push import send_notifications
from src.screener.bucket_breakouts import score_bucket_1
from src.screener.bucket_fundamentals import score_bucket_3
from src.screener.bucket_swings import score_bucket_2
from src.screener.filters import passes_cooldown_filter, passes_universe_filter
from src.tls import configure_system_tls
from src.validate import validate_report


def _parse_date(date_str: str | None) -> dt.date:
    return dt.date.fromisoformat(date_str) if date_str else dt.date.today()


def _fetch_yf_history_bars(ticker: str, start_date_iso: str) -> list[dict[str, Any]]:
    import yfinance as yf

    df = yf.download(ticker, start=start_date_iso, interval="1d", auto_adjust=False, progress=False)
    if df is None or df.empty:
        return []
    bars: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        d_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        bars.append(
            {
                "date": d_str,
                "high": float(row["High"].iloc[0] if hasattr(row["High"], "iloc") else row["High"]),
                "low": float(row["Low"].iloc[0] if hasattr(row["Low"], "iloc") else row["Low"]),
                "close": float(row["Close"].iloc[0] if hasattr(row["Close"], "iloc") else row["Close"]),
            }
        )
    return bars


def cmd_grade_ledger(args: argparse.Namespace) -> int:
    as_of = _parse_date(args.date)
    ledger = SignalLedger()
    count = ledger.grade_ledger(_fetch_yf_history_bars, as_of=as_of)
    scorecard = ledger.get_30d_scorecard(as_of=as_of)
    print(json.dumps({"graded_signals": count, "scorecard_30d": scorecard}, indent=2))
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    load_env()
    as_of = _parse_date(args.date)
    cfg = load_universe_config()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] if args.tickers else list(cfg.smoke_tickers)
    if not tickers:
        print("No tickers specified for ingestion.", file=sys.stderr)
        return 1

    # Always include benchmarks SPY and IWM for macro regime context
    fetch_list = list(dict.fromkeys(tickers + ["SPY", "IWM"]))
    snapshots = fetch_batch_market_snapshots(fetch_list)
    finra_maps = fetch_recent_finra_regsho(as_of, lookback_trading_days=5 if args.smoke else 20)
    sec_client = SecEdgarClient()
    cache = MarketCacheStore()

    ingested = 0
    for sym in tickers:
        snap = snapshots.get(sym)
        if snap is None:
            continue
        public_float_usd = (snap.float_shares * snap.close) if (snap.float_shares and snap.close) else None
        sec_profile = sec_client.fetch_ticker_sec_profile(
            ticker=sym,
            as_of=as_of,
            market_cap=snap.market_cap,
            public_float_usd=public_float_usd,
            dilution_lookback_days=cfg.dilution_lookback_days,
            max_form4_xmls=3 if args.smoke else 6,
        )
        short_summary = summarize_short_trends(finra_maps, sym, float_shares=snap.float_shares)
        earnings_sig = fetch_finnhub_earnings_signal(sym, as_of)

        record = {
            "ticker": sym,
            "run_date": as_of.isoformat(),
            "market": snap.to_dict(),
            "sec": sec_profile,
            "short_interest": short_summary,
            "earnings": earnings_sig,
        }
        cache.upsert_record(as_of.isoformat(), sym, record)
        ingested += 1

    # Persist benchmark macro snapshot
    iwm_snap = snapshots.get("IWM")
    macro = fetch_macro_context(
        as_of=as_of,
        iwm_close=iwm_snap.close if iwm_snap else None,
        iwm_sma50=iwm_snap.sma50 if iwm_snap else None,
    )
    cache.upsert_record(as_of.isoformat(), "__MACRO__", macro.to_dict())

    print(json.dumps({"run_date": as_of.isoformat(), "ingested_tickers": ingested, "macro": macro.to_dict()}, indent=2))
    return 0


def _render_sample_report(candidates_payload: dict[str, Any]) -> str:
    run_date = candidates_payload["run_date"]
    macro = candidates_payload.get("macro", {})
    lines = [
        f"# Equity Pulse Daily Research Brief — {run_date}",
        "",
        "> Strictly research-only. All quantitative figures sourced deterministically from `candidates_"
        + f"{run_date}.json`.",
        "",
        "## Regime & 30-Day Scorecard",
        f"- **Macro Regime**: VIX={macro.get('vix', 'N/A')} | IWM < SMA50={macro.get('iwm_below_sma50', False)} | Choppy={macro.get('choppy_regime', False)}",
        f"- **Universe Screened**: {candidates_payload['stats']['total_loaded']} loaded, {candidates_payload['stats']['passed_universe']} passed liquidity/blocklist gates.",
        "",
        "## Screened Candidates",
        "",
    ]

    any_card = False
    for b_idx in (1, 2, 3):
        items = candidates_payload["buckets"].get(f"bucket_{b_idx}", [])
        if not items:
            continue
        any_card = True
        for c in items:
            direction = c["direction"]
            lines.append(f"### [{direction}] {c['ticker']} — Bucket {b_idx} ({c['horizon']})")
            lines.append(f"- **Ref Price**: ${c['ref_price']:.2f} | **RVOL**: {c['rvol_20d']:.2f}x | **Market Cap**: ${c['market_cap']:,.0f}")
            lines.append(
                f"- **Entry Zone**: ${c['entry_low']:.2f} - ${c['entry_high']:.2f} | "
                f"**Stop Price**: ${c['stop_price']:.2f} | **Target Price**: ${c['target_price']:.2f} "
                f"(R:R {c['reward_risk_ratio']:.2f})"
            )
            lines.append(f"- **Deterministic Drivers**: {'; '.join(c.get('reasons', []))}")
            if c.get("warnings"):
                lines.append(f"- **Risk Flags**: {'; '.join(c['warnings'])}")
            if direction == "LONG":
                lines.append("- **How this fails (Invalidation)**: Break below stop level or adverse SEC/dilution catalyst.")
            else:
                lines.append("- **Squeeze / Upside Hazard**: Monitor float liquidity, FINRA short-vol spikes, and upside binary news.")
            lines.append("")

    if not any_card:
        lines.append("_No tickers met all strict Stage 1 bucket thresholds in this sample universe run._")
        lines.append("")

    return "\n".join(lines)


def cmd_screen(args: argparse.Namespace) -> int:
    as_of = _parse_date(args.date)
    run_date = as_of.isoformat()
    cfg = load_universe_config()
    cache = MarketCacheStore()
    ledger = SignalLedger()

    records = cache.load_records(run_date)
    macro = records.pop("__MACRO__", {"as_of": run_date, "choppy_regime": False})

    b1: list[dict[str, Any]] = []
    b2: list[dict[str, Any]] = []
    b3: list[dict[str, Any]] = []
    rejected: dict[str, str] = {}
    passed_count = 0

    for ticker, rec in sorted(records.items()):
        ok, reason = passes_universe_filter(rec, cfg)
        if not ok:
            rejected[ticker] = reason
            continue
        passed_count += 1
        has_24h_sec = bool(rec.get("sec", {}).get("has_recent_24h_filing"))

        for bucket_idx, scorer, sink in (
            (1, score_bucket_1, b1),
            (2, score_bucket_2, b2),
            (3, score_bucket_3, b3),
        ):
            cd_ok, _ = passes_cooldown_filter(ticker, bucket_idx, as_of, has_24h_sec, cfg, ledger)
            if not cd_ok:
                continue
            sink.extend(scorer(rec, cfg))

    cap = cfg.max_candidates_per_bucket
    b1.sort(key=lambda x: x["score"], reverse=True)
    b2.sort(key=lambda x: x["score"], reverse=True)
    b3.sort(key=lambda x: x["score"], reverse=True)

    payload = {
        "run_date": run_date,
        "macro": macro,
        "scorecard_30d": ledger.get_30d_scorecard(as_of),
        "stats": {
            "total_loaded": len(records),
            "passed_universe": passed_count,
            "rejected": rejected,
        },
        "buckets": {
            "bucket_1": b1[:cap],
            "bucket_2": b2[:cap],
            "bucket_3": b3[:cap],
        },
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"candidates_{run_date}.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result_summary: dict[str, Any] = {
        "candidates_path": str(out_path),
        "counts": {"bucket_1": len(payload["buckets"]["bucket_1"]), "bucket_2": len(payload["buckets"]["bucket_2"]), "bucket_3": len(payload["buckets"]["bucket_3"])},
    }

    if args.write_sample_report:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        rep_path = REPORTS_DIR / f"{run_date}.md"
        rep_path.write_text(_render_sample_report(payload), encoding="utf-8")
        result_summary["report_path"] = str(rep_path)

    print(json.dumps(result_summary, indent=2))
    return 0


def cmd_validate_report(args: argparse.Namespace) -> int:
    res = validate_report(Path(args.report), Path(args.candidates))
    print(
        json.dumps(
            {"ok": res.ok, "cards_checked": res.cards_checked, "errors": res.errors},
            indent=2,
        )
    )
    return 0 if res.ok else 1


def cmd_record_ledger(args: argparse.Namespace) -> int:
    run_date = _parse_date(args.date).isoformat()
    res = validate_report(Path(args.report), Path(args.candidates))
    if not res.ok:
        print(json.dumps({"recorded": 0, "errors": res.errors}, indent=2), file=sys.stderr)
        return 1
    ledger = SignalLedger()
    ids = []
    for sig in res.parsed_signals:
        sig["run_date"] = run_date
        ids.append(ledger.record_signal(sig))
    print(json.dumps({"recorded": len(ids), "signal_ids": ids}, indent=2))
    return 0


def cmd_notify(args: argparse.Namespace) -> int:
    out = send_notifications(Path(args.report), dry_run=args.dry_run)
    print(json.dumps(out, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="equity-pulse", description="Daily Long/Short US Equity Research CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_grade = sub.add_parser("grade-ledger", help="Grade open/historical signals and output 30d scorecard")
    p_grade.add_argument("--date", default=None, help="As-of date YYYY-MM-DD (default: today)")
    p_grade.set_defaults(func=cmd_grade_ledger)

    p_ingest = sub.add_parser("ingest", help="Ingest OHLCV, SEC EDGAR, FINRA Reg SHO, and optional macro feeds")
    p_ingest.add_argument("--date", default=None, help="Run date YYYY-MM-DD (default: today)")
    p_ingest.add_argument("--smoke", action="store_true", help="Fast smoke ingestion on sample universe")
    p_ingest.add_argument("--tickers", default=None, help="Comma-separated ticker override")
    p_ingest.set_defaults(func=cmd_ingest)

    p_screen = sub.add_parser("screen", help="Run Stage 1 deterministic 3-bucket screener")
    p_screen.add_argument("--date", default=None, help="Run date YYYY-MM-DD (default: today)")
    p_screen.add_argument("--write-sample-report", action="store_true", help="Also write a deterministic sample report to reports/")
    p_screen.set_defaults(func=cmd_screen)

    p_val = sub.add_parser("validate-report", help="Run deterministic fact & R:R validator on a report")
    p_val.add_argument("--report", required=True, help="Path to reports/YYYY-MM-DD.md")
    p_val.add_argument("--candidates", required=True, help="Path to data/candidates_YYYY-MM-DD.json")
    p_val.set_defaults(func=cmd_validate_report)

    p_rec = sub.add_parser("record-ledger", help="Validate report and record surviving signals into SQLite ledger")
    p_rec.add_argument("--report", required=True, help="Path to reports/YYYY-MM-DD.md")
    p_rec.add_argument("--candidates", required=True, help="Path to data/candidates_YYYY-MM-DD.json")
    p_rec.add_argument("--date", default=None, help="Run date YYYY-MM-DD (default: today)")
    p_rec.set_defaults(func=cmd_record_ledger)

    p_not = sub.add_parser("notify", help="Format and dispatch mobile push digest")
    p_not.add_argument("--report", required=True, help="Path to reports/YYYY-MM-DD.md")
    p_not.add_argument("--dry-run", action="store_true", help="Print digest without sending network requests")
    p_not.set_defaults(func=cmd_notify)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_system_tls()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
