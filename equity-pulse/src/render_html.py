"""Self-contained graphic HTML web dashboard generator for Equity Pulse daily reports."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from src.config import REPORTS_DIR
from src.validate import CARD_HEADER_RE, PRICE_LINE_RE


def _esc(val: Any) -> str:
    return html.escape(str(val if val is not None else ""), quote=True)


def _fmt_mcap(mcap: float | None) -> str:
    if mcap is None:
        return "N/A"
    v = float(mcap)
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    return f"${v:,.0f}"


def _extract_bullet(block: str, label: str) -> str:
    m = re.search(rf"-\s+\*\*{re.escape(label)}\*\*:\s*(.+)", block)
    return m.group(1).strip() if m else ""


def _parse_cards_from_report(
    report_text: str,
    candidates_payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Extract structured cards from reports/YYYY-MM-DD.md enriched with candidates JSON metadata."""
    cand_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    if candidates_payload:
        for bkey in ("bucket_1", "bucket_2", "bucket_3"):
            for item in candidates_payload.get("buckets", {}).get(bkey, []):
                cand_lookup[(str(item["ticker"]).upper(), str(item["direction"]).upper())] = item

    cards: list[dict[str, Any]] = []
    matches = list(CARD_HEADER_RE.finditer(report_text))
    for i, m in enumerate(matches):
        direction = m.group(1).upper()
        ticker = m.group(2).upper()
        bucket = int(m.group(3))
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(report_text)
        block = report_text[start:end]

        truth = cand_lookup.get((ticker, direction), {})

        def _num(key: str, fallback: Any = None) -> float | None:
            hit = PRICE_LINE_RE[key].search(block)
            if hit:
                return float(hit.group(1).replace(",", ""))
            val = truth.get(key)
            return float(val) if val is not None else fallback

        ref_p = _num("ref_price", 0.0) or 0.0
        entry_lo = _num("entry_low", ref_p) or ref_p
        entry_hi = _num("entry_high", entry_lo) or entry_lo
        stop_p = _num("stop_price", ref_p) or ref_p
        target_p = _num("target_price", ref_p) or ref_p
        rvol = _num("rvol_20d", truth.get("rvol_20d"))

        entry_mid = (entry_lo + entry_hi) / 2.0
        if direction == "LONG":
            rr = (target_p - entry_mid) / max(entry_mid - stop_p, 0.01)
        else:
            rr = (entry_mid - target_p) / max(stop_p - entry_mid, 0.01)

        horizon_m = re.search(r"Bucket\s+[123]\s*\(([^)]+)\)", block)
        horizon = horizon_m.group(1) if horizon_m else truth.get("horizon", "")

        plain = _extract_bullet(block, "Plain-English Takeaway")
        drivers = _extract_bullet(block, "Deterministic Drivers") or "; ".join(truth.get("reasons", []))
        thesis = _extract_bullet(block, "Forensic Thesis")
        risk_flags = _extract_bullet(block, "Risk Flags") or "; ".join(truth.get("warnings", []))
        counter = (
            _extract_bullet(block, "How this fails (Invalidation)")
            if direction == "LONG"
            else _extract_bullet(block, "Squeeze / Upside Hazard")
        )

        cards.append(
            {
                "ticker": ticker,
                "direction": direction,
                "bucket": bucket,
                "horizon": horizon,
                "strategy_style": truth.get("strategy_style")
                or ("Investing (Long)" if direction == "LONG" else "Forensic / Tactical Short"),
                "score": truth.get("score", 75.0),
                "market_cap": truth.get("market_cap"),
                "ref_price": ref_p,
                "entry_low": entry_lo,
                "entry_high": entry_hi,
                "stop_price": stop_p,
                "target_price": target_p,
                "reward_risk_ratio": round(rr, 2),
                "rvol_20d": rvol,
                "plain_english": plain,
                "drivers": drivers,
                "thesis": thesis,
                "risk_flags": risk_flags,
                "counter_thesis": counter,
                "sec_urls": truth.get("sec_urls", []),
            }
        )
    return cards


def _svg_price_ladder(card: dict[str, Any]) -> str:
    """Render an inline SVG visual Risk/Reward price ladder showing Stop, Entry, Ref, and Target."""
    direction = card["direction"]
    ref_p = card["ref_price"]
    stop_p = card["stop_price"]
    target_p = card["target_price"]
    lo = min(ref_p, stop_p, target_p, card["entry_low"])
    hi = max(ref_p, stop_p, target_p, card["entry_high"])
    span = max(hi - lo, 0.01)
    pad = span * 0.08
    min_v = lo - pad
    max_v = hi + pad
    rng = max(max_v - min_v, 0.01)

    def x_pos(price: float) -> float:
        return round(24.0 + ((price - min_v) / rng) * 352.0, 1)

    x_stop = x_pos(stop_p)
    x_ref = x_pos(ref_p)
    x_tgt = x_pos(target_p)

    risk_x1, risk_x2 = sorted([x_stop, x_ref])
    rew_x1, rew_x2 = sorted([x_ref, x_tgt])
    rew_color = "#10b981" if direction == "LONG" else "#f43f5e"
    risk_color = "#64748b"

    return (
        '<svg class="ladder-svg" viewBox="0 0 400 72" role="img" aria-label="Price Risk Reward Ladder">'
        '<line x1="24" y1="36" x2="376" y2="36" stroke="#334155" stroke-width="6" stroke-linecap="round"/>'
        f'<line x1="{risk_x1}" y1="36" x2="{risk_x2}" y2="36" stroke="{risk_color}" stroke-width="8" stroke-linecap="round"/>'
        f'<line x1="{rew_x1}" y1="36" x2="{rew_x2}" y2="36" stroke="{rew_color}" stroke-width="8" stroke-linecap="round"/>'
        f'<circle cx="{x_stop}" cy="36" r="6" fill="#ef4444" stroke="#0f172a" stroke-width="2"/>'
        f'<circle cx="{x_ref}" cy="36" r="6" fill="#f8fafc" stroke="#0f172a" stroke-width="2"/>'
        f'<circle cx="{x_tgt}" cy="36" r="6" fill="#10b981" stroke="#0f172a" stroke-width="2"/>'
        f'<text x="{x_stop}" y="18" text-anchor="middle" fill="#fca5a5" font-size="10" font-weight="600">Stop ${stop_p:.2f}</text>'
        f'<text x="{x_ref}" y="62" text-anchor="middle" fill="#e2e8f0" font-size="10" font-weight="700">Ref ${ref_p:.2f}</text>'
        f'<text x="{x_tgt}" y="18" text-anchor="middle" fill="#6ee7b7" font-size="10" font-weight="600">Target ${target_p:.2f}</text>'
        "</svg>"
    )


def _svg_score_chart(cards: list[dict[str, Any]]) -> str:
    """Render an inline SVG horizontal bar chart comparing setup scores across Investing & Shorts."""
    if not cards:
        return ""
    row_h = 30
    height = max(60, 24 + len(cards) * row_h)
    bars: list[str] = []
    for idx, c in enumerate(cards[:10]):
        y = 14 + idx * row_h
        score = max(0.0, min(100.0, float(c.get("score") or 70.0)))
        bar_w = round((score / 100.0) * 260.0, 1)
        color = "#10b981" if c["direction"] == "LONG" else "#f43f5e"
        label = f"{c['ticker']} ({c['direction']})"
        bars.append(
            f'<text x="6" y="{y + 15}" fill="#cbd5e1" font-size="11" font-weight="600">{_esc(label)}</text>'
            f'<rect x="118" y="{y + 3}" width="260" height="16" rx="4" fill="#1e293b"/>'
            f'<rect x="118" y="{y + 3}" width="{bar_w}" height="16" rx="4" fill="{color}"/>'
            f'<text x="386" y="{y + 15}" fill="#f8fafc" font-size="11" font-weight="600">{score:.0f}</text>'
        )
    return (
        f'<svg class="chart-svg" viewBox="0 0 420 {height}" role="img" aria-label="Candidate Score Comparison">'
        + "".join(bars)
        + "</svg>"
    )


def _render_card_html(c: dict[str, Any]) -> str:
    is_long = c["direction"] == "LONG"
    badge_cls = "badge-long" if is_long else "badge-short"
    side_label = "INVESTING LONG" if is_long else "SHORT"
    counter_label = "How This Fails (Invalidation)" if is_long else "Squeeze / Upside Hazard"

    sec_links_html = ""
    if c.get("sec_urls"):
        links = " ".join(
            f'<a class="sec-link" href="{_esc(u)}" target="_blank" rel="noopener">SEC Filing {idx + 1}</a>'
            for idx, u in enumerate(c["sec_urls"][:3])
        )
        sec_links_html = f'<div class="sec-links">{links}</div>'

    flag_html = (
        f'<div class="risk-flag"><strong>Risk Flag:</strong> {_esc(c["risk_flags"])}</div>'
        if c.get("risk_flags")
        else ""
    )
    rvol_str = f"{c['rvol_20d']:.2f}x" if c.get("rvol_20d") is not None else "N/A"

    return f"""
    <article class="card" data-side="{_esc(c['direction'])}" data-bucket="{c['bucket']}">
      <div class="card-top">
        <div>
          <span class="badge {badge_cls}">{side_label}</span>
          <span class="bucket-pill">Bucket {c['bucket']} · {_esc(c['horizon'])}</span>
          <h3 class="ticker">{_esc(c['ticker'])}</h3>
        </div>
        <div class="rr-box">
          <div class="rr-val">{c['reward_risk_ratio']:.2f}x</div>
          <div class="rr-lbl">Reward / Risk</div>
        </div>
      </div>

      <div class="plain-box">
        <div class="plain-title">Plain-English Takeaway</div>
        <p>{_esc(c['plain_english'] or c['drivers'])}</p>
      </div>

      {_svg_price_ladder(c)}

      <div class="metrics-grid">
        <div><span class="m-lbl">Ref Price</span><strong>${c['ref_price']:.2f}</strong></div>
        <div><span class="m-lbl">Entry Zone</span><strong>${c['entry_low']:.2f}–${c['entry_high']:.2f}</strong></div>
        <div><span class="m-lbl">Market Cap</span><strong>{_fmt_mcap(c.get('market_cap'))}</strong></div>
        <div><span class="m-lbl">RVOL (20d)</span><strong>{rvol_str}</strong></div>
      </div>

      <div class="drivers"><strong>Drivers:</strong> {_esc(c['drivers'])}</div>
      {flag_html}
      <div class="counter"><strong>{counter_label}:</strong> {_esc(c['counter_thesis'])}</div>
      {sec_links_html}
    </article>
    """


def render_report_html(
    report_text: str,
    candidates_payload: dict[str, Any] | None = None,
    available_dates: list[str] | None = None,
) -> str:
    """Render a daily Markdown report + candidates JSON into a standalone graphic HTML dashboard."""
    payload = candidates_payload or {}
    run_date = payload.get("run_date") or (
        re.search(r"(\d{4}-\d{2}-\d{2})", report_text).group(1)
        if re.search(r"(\d{4}-\d{2}-\d{2})", report_text)
        else "Latest"
    )
    macro = payload.get("macro", {})
    prev_acc = payload.get("previous_day_accuracy", {})
    stats = payload.get("stats", {})

    choppy = bool(macro.get("choppy_regime", False))
    market_mood = _extract_bullet(report_text, "Market Mood") or (
        "Small-cap stocks (IWM) are trading below their 50-day trend—choppy regime, be selective."
        if choppy
        else "Broad small-cap trend is steady—normal conviction thresholds apply."
    )

    cards = _parse_cards_from_report(report_text, payload)
    long_cards = [c for c in cards if c["direction"] == "LONG"]
    short_cards = [c for c in cards if c["direction"] == "SHORT"]

    # Prior-day accuracy panel
    prev_items = prev_acc.get("items", [])
    hit_rate = prev_acc.get("hit_rate_pct")
    profit_factor = prev_acc.get("profit_factor")
    hit_display = f"{hit_rate:.1f}%" if hit_rate is not None else "Pending"
    pf_display = f"{profit_factor:.2f}" if profit_factor is not None else "—"
    hit_bar_w = max(0.0, min(100.0, float(hit_rate or 0.0)))

    if prev_items:
        rows_html = []
        for it in prev_items:
            v = str(it.get("verdict", "PENDING")).upper()
            v_cls = "verdict-ok" if v == "CORRECT" else ("verdict-bad" if v == "INCORRECT" else "verdict-wait")
            lp = f"${it['latest_price']:.2f}" if it.get("latest_price") is not None else "Pending"
            nr = f"{it['next_day_return_pct']:+.2f}%" if it.get("next_day_return_pct") is not None else "Pending"
            rows_html.append(
                f"<tr><td><strong>{_esc(it['ticker'])}</strong></td>"
                f"<td>{_esc(it['direction'])}</td>"
                f"<td>B{it['bucket']}</td>"
                f"<td>${it['ref_price']:.2f}</td>"
                f"<td>{lp}</td>"
                f"<td>{nr}</td>"
                f'<td><span class="verdict {v_cls}">{_esc(v)}</span></td>'
                f"<td>{_esc(it.get('outcome_status', 'OPEN'))}</td></tr>"
            )
        prev_table_html = (
            '<div class="table-wrap"><table><thead>'
            "<tr><th>Ticker</th><th>Side</th><th>Bucket</th><th>Ref Price</th><th>Next Close</th><th>Signed Return</th><th>Verdict</th><th>Status</th></tr>"
            "</thead><tbody>"
            + "".join(rows_html)
            + "</tbody></table></div>"
        )
    else:
        prev_table_html = (
            f'<p class="muted">{_esc(prev_acc.get("plain_english_summary", "No prior-day recommendations recorded yet—today’s logged picks will be graded automatically on the next trading day."))}</p>'
        )

    # Rejected / blocklisted panel
    rejected_map = stats.get("rejected", {})
    rejected_items = "".join(
        f'<li><strong>{_esc(sym)}</strong> — {_esc(reason.replace("_", " "))}</li>'
        for sym, reason in list(rejected_map.items())[:12]
    )
    rejected_html = (
        f'<ul class="rej-list">{rejected_items}</ul>'
        if rejected_items
        else '<p class="muted">No tickers were rejected by blocklist/liquidity filters in this snapshot.</p>'
    )

    date_links = ""
    if available_dates:
        opts = "".join(
            f'<a class="date-chip {"active" if d == run_date else ""}" href="{_esc(d)}.html">{_esc(d)}</a>'
            for d in sorted(available_dates, reverse=True)[:10]
        )
        date_links = f'<div class="date-nav">{opts}</div>'

    long_html = (
        "".join(_render_card_html(c) for c in long_cards)
        if long_cards
        else '<div class="empty-card">No Investing (Long) recommendations passed all strict thresholds in this scan.</div>'
    )
    short_html = (
        "".join(_render_card_html(c) for c in short_cards)
        if short_cards
        else '<div class="empty-card">No Short recommendations passed all strict thresholds in this scan.</div>'
    )

    mood_cls = "mood-choppy" if choppy else "mood-steady"
    mood_tag = "CHOPPY REGIME" if choppy else "STEADY REGIME"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Equity Pulse Daily Research Dashboard — {_esc(run_date)}</title>
  <style>
    :root {{
      --bg: #0b1120;
      --panel: #111827;
      --card: #162033;
      --border: #24324a;
      --text: #f1f5f9;
      --muted: #94a3b8;
      --long: #10b981;
      --short: #f43f5e;
      --warn: #f59e0b;
      --accent: #38bdf8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif;
      background: radial-gradient(circle at top right, #172554 0%, var(--bg) 42%);
      color: var(--text);
      line-height: 1.5;
    }}
    .wrap {{ max-width: 1220px; margin: 0 auto; padding: 28px 20px 64px; }}
    header {{
      display: flex; flex-wrap: wrap; justify-content: space-between; align-items: flex-start;
      gap: 16px; margin-bottom: 22px; border-bottom: 1px solid var(--border); padding-bottom: 18px;
    }}
    h1 {{ margin: 0; font-size: 1.75rem; letter-spacing: -0.02em; }}
    .subtitle {{ color: var(--muted); font-size: 0.92rem; margin-top: 4px; }}
    .mood-pill {{
      padding: 6px 12px; border-radius: 999px; font-size: 0.78rem; font-weight: 700; letter-spacing: 0.04em;
    }}
    .mood-choppy {{ background: rgba(245, 158, 11, 0.16); color: #fcd34d; border: 1px solid rgba(245, 158, 11, 0.45); }}
    .mood-steady {{ background: rgba(16, 185, 129, 0.16); color: #6ee7b7; border: 1px solid rgba(16, 185, 129, 0.45); }}
    .date-nav {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
    .date-chip {{
      text-decoration: none; color: var(--muted); background: var(--panel); border: 1px solid var(--border);
      padding: 4px 10px; border-radius: 8px; font-size: 0.8rem;
    }}
    .date-chip.active {{ color: #fff; border-color: var(--accent); background: rgba(56, 189, 248, 0.15); }}

    .kpi-row {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(185px, 1fr)); gap: 14px; margin-bottom: 22px;
    }}
    .kpi {{
      background: var(--panel); border: 1px solid var(--border); border-radius: 14px; padding: 16px;
    }}
    .kpi-lbl {{ color: var(--muted); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; }}
    .kpi-val {{ font-size: 1.65rem; font-weight: 800; margin-top: 4px; }}
    .kpi-long {{ color: var(--long); }}
    .kpi-short {{ color: var(--short); }}
    .kpi-accent {{ color: var(--accent); }}

    .panel {{
      background: var(--panel); border: 1px solid var(--border); border-radius: 16px;
      padding: 20px; margin-bottom: 22px;
    }}
    .panel h2 {{ margin: 0 0 12px; font-size: 1.15rem; }}
    .split-2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 18px; }}

    .gauge-track {{
      height: 10px; border-radius: 999px; background: #1e293b; overflow: hidden; margin: 10px 0 14px;
    }}
    .gauge-fill {{ height: 100%; background: linear-gradient(90deg, #38bdf8, #10b981); }}

    .filters {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 18px; }}
    .filter-btn {{
      background: var(--panel); color: var(--text); border: 1px solid var(--border);
      padding: 7px 14px; border-radius: 10px; cursor: pointer; font-weight: 600; font-size: 0.85rem;
    }}
    .filter-btn.active {{ background: var(--accent); color: #0b1120; border-color: var(--accent); }}

    .section-title {{
      font-size: 1.25rem; margin: 26px 0 14px; display: flex; align-items: center; gap: 10px;
    }}
    .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
    .dot-long {{ background: var(--long); }}
    .dot-short {{ background: var(--short); }}

    .cards-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 18px; }}
    .card {{
      background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 18px;
      display: flex; flex-direction: column; gap: 12px;
    }}
    .card-top {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }}
    .ticker {{ margin: 6px 0 0; font-size: 1.45rem; letter-spacing: 0.02em; }}
    .badge {{
      display: inline-block; padding: 3px 9px; border-radius: 6px; font-size: 0.72rem; font-weight: 800; letter-spacing: 0.04em;
    }}
    .badge-long {{ background: rgba(16, 185, 129, 0.18); color: #6ee7b7; border: 1px solid rgba(16, 185, 129, 0.45); }}
    .badge-short {{ background: rgba(244, 63, 94, 0.18); color: #fda4af; border: 1px solid rgba(244, 63, 94, 0.45); }}
    .bucket-pill {{ color: var(--muted); font-size: 0.78rem; margin-left: 6px; }}
    .rr-box {{ text-align: right; background: rgba(15, 23, 42, 0.7); padding: 8px 12px; border-radius: 10px; border: 1px solid var(--border); }}
    .rr-val {{ font-size: 1.15rem; font-weight: 800; color: var(--accent); }}
    .rr-lbl {{ font-size: 0.7rem; color: var(--muted); }}

    .plain-box {{
      background: rgba(56, 189, 248, 0.08); border-left: 3px solid var(--accent);
      padding: 10px 12px; border-radius: 8px;
    }}
    .plain-title {{ font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--accent); font-weight: 700; margin-bottom: 4px; }}
    .plain-box p {{ margin: 0; font-size: 0.9rem; color: #e2e8f0; }}

    .ladder-svg, .chart-svg {{ width: 100%; height: auto; display: block; }}
    .metrics-grid {{
      display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px;
      background: rgba(15, 23, 42, 0.55); border-radius: 10px; padding: 10px 12px;
    }}
    .m-lbl {{ display: block; font-size: 0.72rem; color: var(--muted); }}
    .drivers, .counter, .risk-flag {{ font-size: 0.85rem; color: #cbd5e1; }}
    .risk-flag {{
      background: rgba(245, 158, 11, 0.12); border: 1px solid rgba(245, 158, 11, 0.35);
      color: #fde68a; padding: 8px 10px; border-radius: 8px;
    }}
    .sec-links {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .sec-link {{
      color: var(--accent); font-size: 0.8rem; text-decoration: none;
      border: 1px solid rgba(56, 189, 248, 0.35); padding: 3px 9px; border-radius: 6px;
    }}
    .empty-card {{
      background: var(--panel); border: 1px dashed var(--border); border-radius: 14px;
      padding: 18px; color: var(--muted); font-size: 0.9rem;
    }}

    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.86rem; }}
    th, td {{ padding: 9px 10px; border-bottom: 1px solid var(--border); text-align: left; }}
    th {{ color: var(--muted); font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.04em; }}
    .verdict {{ padding: 2px 8px; border-radius: 6px; font-weight: 700; font-size: 0.75rem; }}
    .verdict-ok {{ background: rgba(16, 185, 129, 0.18); color: #6ee7b7; }}
    .verdict-bad {{ background: rgba(244, 63, 94, 0.18); color: #fda4af; }}
    .verdict-wait {{ background: rgba(148, 163, 184, 0.18); color: #cbd5e1; }}
    .muted {{ color: var(--muted); margin: 0; font-size: 0.9rem; }}
    .rej-list {{ margin: 0; padding-left: 18px; color: var(--muted); font-size: 0.86rem; columns: 2; }}
    footer {{ margin-top: 34px; color: var(--muted); font-size: 0.8rem; text-align: center; }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>Equity Pulse — Daily Research Dashboard</h1>
        <div class="subtitle">Run Date: <strong>{_esc(run_date)}</strong> · Strictly Research-Only · Deterministic Metrics Verified</div>
        {date_links}
      </div>
      <span class="mood-pill {mood_cls}">{mood_tag}</span>
    </header>

    <section class="kpi-row">
      <div class="kpi"><div class="kpi-lbl">Investing (Long)</div><div class="kpi-val kpi-long">{len(long_cards)}</div></div>
      <div class="kpi"><div class="kpi-lbl">Short Setups</div><div class="kpi-val kpi-short">{len(short_cards)}</div></div>
      <div class="kpi"><div class="kpi-lbl">Prior-Day Hit Rate</div><div class="kpi-val kpi-accent">{_esc(hit_display)}</div></div>
      <div class="kpi"><div class="kpi-lbl">Profit Factor</div><div class="kpi-val">{_esc(pf_display)}</div></div>
      <div class="kpi"><div class="kpi-lbl">Universe Checked</div><div class="kpi-val">{stats.get('total_loaded', len(cards))}</div></div>
    </section>

    <div class="split-2">
      <section class="panel">
        <h2>Plain-English Market Mood</h2>
        <p style="margin:0; color:#e2e8f0;">{_esc(market_mood)}</p>
        <div style="margin-top:14px;">
          <div class="kpi-lbl">Prior-Day Accuracy Summary</div>
          <div class="gauge-track"><div class="gauge-fill" style="width:{hit_bar_w}%"></div></div>
          <p class="muted">{_esc(prev_acc.get('plain_english_summary', 'No prior-day calls graded yet.'))}</p>
        </div>
      </section>

      <section class="panel">
        <h2>Candidate Score Comparison</h2>
        {_svg_score_chart(cards) if cards else '<p class="muted">No candidates scored today.</p>'}
      </section>
    </div>

    <section class="panel">
      <h2>Prior-Day Recommendation Accuracy Review</h2>
      {prev_table_html}
    </section>

    <div class="filters" role="group" aria-label="Filter recommendations">
      <button class="filter-btn active" onclick="filterCards('ALL', this)">All ({len(cards)})</button>
      <button class="filter-btn" onclick="filterCards('LONG', this)">Investing Long ({len(long_cards)})</button>
      <button class="filter-btn" onclick="filterCards('SHORT', this)">Shorts ({len(short_cards)})</button>
    </div>

    <h2 class="section-title"><span class="dot dot-long"></span> Investing Recommendations (Long)</h2>
    <section class="cards-grid" id="long-grid">{long_html}</section>

    <h2 class="section-title"><span class="dot dot-short"></span> Short Recommendations</h2>
    <section class="cards-grid" id="short-grid">{short_html}</section>

    <section class="panel" style="margin-top:26px;">
      <h2>Filtered &amp; Blocklisted Tickers</h2>
      {rejected_html}
    </section>

    <footer>
      Equity Pulse Research Agent · Research-only dashboard generated from <code>reports/{_esc(run_date)}.md</code> and <code>data/candidates_{_esc(run_date)}.json</code>.
    </footer>
  </div>

  <script>
    function filterCards(side, btn) {{
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      document.querySelectorAll('.card').forEach(el => {{
        el.style.display = (side === 'ALL' || el.dataset.side === side) ? 'flex' : 'none';
      }});
    }}
  </script>
</body>
</html>
"""


def write_report_dashboard(
    report_path: Path,
    candidates_path: Path | None = None,
    output_dir: Path = REPORTS_DIR,
) -> tuple[Path, Path]:
    """Write `reports/YYYY-MM-DD.html` and update `reports/index.html` to the latest dashboard."""
    rep_path = Path(report_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report_text = rep_path.read_text(encoding="utf-8")
    candidates_payload: dict[str, Any] | None = None
    if candidates_path and Path(candidates_path).exists():
        candidates_payload = json.loads(Path(candidates_path).read_text(encoding="utf-8"))

    run_date = rep_path.stem
    existing_dates = sorted(
        {p.stem for p in out_dir.glob("????-??-??.html")} | {run_date}
    )
    html_doc = render_report_html(report_text, candidates_payload, available_dates=existing_dates)

    dated_html = out_dir / f"{run_date}.html"
    index_html = out_dir / "index.html"
    dated_html.write_text(html_doc, encoding="utf-8")
    index_html.write_text(html_doc, encoding="utf-8")
    return dated_html, index_html
