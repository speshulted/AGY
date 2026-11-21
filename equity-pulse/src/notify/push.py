"""Format daily report into a compact mobile digest and send via configured webhooks/SMTP."""

from __future__ import annotations

import os
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import requests

from src.config import load_env


def format_mobile_digest(report_text: str, max_chars: int = 1800) -> str:
    """Extract title, plain-English summary, regime, and card takeaways into a mobile-friendly digest."""
    lines = report_text.splitlines()
    title = next((ln for ln in lines if ln.startswith("# ")), "# Equity Pulse Daily Digest")
    summary_lines = [ln for ln in lines if re.match(r"^- \*\*(Market Mood|Prior-Day Accuracy|Dual-Strategy|Macro|Regime|Scorecard)", ln)]

    card_entries: list[str] = []
    for idx, ln in enumerate(lines):
        if ln.startswith("### ["):
            headline = ln.replace("### ", "")
            plain = ""
            for nxt in lines[idx + 1 : idx + 6]:
                if nxt.startswith("### ["):
                    break
                if "**Plain-English Takeaway**:" in nxt:
                    plain = nxt.split("**Plain-English Takeaway**:", 1)[1].strip()
                    break
            card_entries.append(f"- **{headline}**" + (f" — {plain}" if plain else ""))

    parts = [title]
    if summary_lines:
        parts.append("\n".join(summary_lines[:4]))
    if card_entries:
        parts.append("**Top Setups:**\n" + "\n".join(card_entries[:10]))
    else:
        parts.append("No setups passed all screening & risk-critic gates today.")

    digest = "\n\n".join(parts)
    if len(digest) > max_chars:
        digest = digest[: max_chars - 3] + "..."
    return digest


def send_notifications(
    report_path: Path,
    dry_run: bool = False,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Dispatch mobile digest to any configured Discord, Slack, Telegram, or SMTP channel."""
    load_env()
    text = Path(report_path).read_text(encoding="utf-8")
    digest = format_mobile_digest(text)

    if dry_run:
        return {"dry_run": True, "sent": [], "digest": digest, "email_body": text}

    sent: list[str] = []
    errors: list[str] = []

    discord_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if discord_url:
        try:
            r = requests.post(discord_url, json={"content": digest}, timeout=timeout)
            r.raise_for_status()
            sent.append("discord")
        except Exception as exc:
            errors.append(f"discord: {exc}")

    slack_url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if slack_url:
        try:
            r = requests.post(slack_url, json={"text": digest}, timeout=timeout)
            r.raise_for_status()
            sent.append("slack")
        except Exception as exc:
            errors.append(f"slack: {exc}")

    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if tg_token and tg_chat:
        try:
            url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
            r = requests.post(url, json={"chat_id": tg_chat, "text": digest}, timeout=timeout)
            r.raise_for_status()
            sent.append("telegram")
        except Exception as exc:
            errors.append(f"telegram: {exc}")

    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_to = os.getenv("SMTP_TO", "").strip()
    if smtp_host and smtp_to:
        try:
            msg = EmailMessage()
            msg["Subject"] = f"Equity Pulse Daily Research Brief — {Path(report_path).stem}"
            msg["From"] = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "equity-pulse@localhost"))
            msg["To"] = smtp_to
            msg.set_content(text)
            port = int(os.getenv("SMTP_PORT", "587"))
            with smtplib.SMTP(smtp_host, port, timeout=timeout) as server:
                server.starttls()
                user = os.getenv("SMTP_USER", "").strip()
                pwd = os.getenv("SMTP_PASS", "").strip()
                if user and pwd:
                    server.login(user, pwd)
                server.send_message(msg)
            sent.append("smtp")
        except Exception as exc:
            errors.append(f"smtp: {exc}")

    return {"dry_run": False, "sent": sent, "errors": errors, "digest": digest}
