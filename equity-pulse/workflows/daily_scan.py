"""Antigravity run_workflow orchestrator for Equity Pulse (Stage 1 -> Stage 2 -> Stage 3)."""

import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

repo_root = Path(args.get("repo_root") or Path.cwd())
if not (repo_root / "src" / "cli.py").exists() and (repo_root / "equity-pulse" / "src" / "cli.py").exists():
    repo_root = repo_root / "equity-pulse"

win_py = repo_root / ".venv" / "Scripts" / "python.exe"
unix_py = repo_root / ".venv" / "bin" / "python"
python_exe = str(win_py if win_py.exists() else (unix_py if unix_py.exists() else sys.executable))

run_date = args.get("date") or dt.date.today().isoformat()
smoke_args = ["--smoke"] if args.get("smoke", False) else []
tickers_args = ["--tickers", str(args["tickers"])] if args.get("tickers") else []
dry_run_args = ["--dry-run"] if args.get("dry_run_notify", False) else []

candidates_path = repo_root / "data" / f"candidates_{run_date}.json"
report_path = repo_root / "reports" / f"{run_date}.md"


async def run_cli(cli_args: list[str], label: str) -> str:
    """Run `python -m src.cli <cli_args>` inside repo_root without requiring `call_tool`."""
    log(f"Starting: {label}")
    proc = await asyncio.create_subprocess_exec(
        python_exe,
        "-m",
        "src.cli",
        *cli_args,
        cwd=str(repo_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        detail = "\n".join(part for part in (stderr_text, stdout_text) if part)
        raise RuntimeError(f"{label} failed (exit {proc.returncode}):\n{detail}")
    log(f"Completed: {label}")
    return stdout_text


phase("Stage 1: Quantitative Grade, Ingest & Screen")
log(f"Running Stage 1 deterministic pipeline for {run_date} in {repo_root}")
await run_cli(["grade-ledger", "--date", run_date], "Grade historical signal ledger")
await run_cli(
    ["ingest", "--date", run_date, *smoke_args, *tickers_args],
    "Ingest market, SEC EDGAR, and FINRA data",
)
await run_cli(["screen", "--date", run_date], "Run deterministic 3-bucket screener")

candidates_data = json.loads(candidates_path.read_text(encoding="utf-8"))
log(
    "Stage 1 candidate counts:",
    {k: len(v) for k, v in candidates_data.get("buckets", {}).items()},
)

phase("Stage 2: Parallel Specialist Subagent Analysis")
specialists = [
    {
        "role": "Catalyst & Swing Analyst (Buckets 1 & 2)",
        "prompt": (
            f"Follow the role spec in `{repo_root / '.agents/agents/catalyst-swing-analyst.md'}` and "
            f"`{repo_root / '.agents/skills/sec-forensics/SKILL.md'}`. "
            f"Read `{candidates_path}` and analyze ONLY `buckets.bucket_1` and `buckets.bucket_2`. "
            "Verify qualitative catalyst context without inventing or altering any numeric metric. "
            "Return Markdown setup cards formatted as `### [LONG|SHORT] TICKER — Bucket N (horizon)` with "
            "a `Plain-English Takeaway` bullet (explaining every technical/filing signal in everyday language), "
            "Ref Price, Entry Zone, Stop Price, Target Price (R:R >= 2.0), and mandatory counter-thesis."
        ),
    },
    {
        "role": "Forensic Fundamental Analyst (Bucket 3)",
        "prompt": (
            f"Follow the role spec in `{repo_root / '.agents/agents/forensic-fundamental-analyst.md'}` and "
            f"`{repo_root / '.agents/skills/sec-forensics/SKILL.md'}`. "
            f"Read `{candidates_path}` and analyze ONLY `buckets.bucket_3`. "
            "Verify fundamental/forensic drivers (FCF/EV, accruals, cash runway, S-3 baby-shelf cap, sector applicability) "
            "without inventing or altering any numeric metric. "
            "Return Markdown setup cards formatted as `### [LONG|SHORT] TICKER — Bucket 3 (1-6m+)` with "
            "a `Plain-English Takeaway` bullet (explaining every accounting/balance-sheet signal in everyday language), "
            "Ref Price, Entry Zone, Stop Price, Target Price (R:R >= 2.0), and mandatory counter-thesis."
        ),
    },
]

drafts = await parallel(
    specialists,
    lambda s: agent(
        s["prompt"],
        schema={"type": "string"},
        role=s["role"],
        type_name="self",
    ),
)

phase("Stage 3: Adversarial Risk Critic & Deterministic Validation")
critic_prompt = (
    f"Follow the role spec in `{repo_root / '.agents/agents/risk-critic.md'}` and rules in `{repo_root / '.agents/AGENTS.md'}`. "
    f"Audit the Stage 2 drafts below against `{candidates_path}` and `{repo_root / 'config/universe.yaml'}`.\n"
    f"Drop weak, sector-inappropriate (e.g., bank Altman Z or biotech XBRL false positives), or non-compliant setups, write the final verified report to `{report_path}` "
    "opening with `## Plain-English Summary`, `## Prior-Day Recommendation Accuracy Review` (using `previous_day_accuracy` verbatim from the candidates JSON), and `## Regime & 30-Day Scorecard`, "
    "followed by separate `## Investing Recommendations (Long)`, `## Short Recommendations`, and `## Rejected / Filtered Candidates` sections where EVERY surviving finding and EVERY rejection includes clear, jargon-free plain-English interpretation alongside exact verified numbers, "
    "and return a brief summary of prior-day accuracy, surviving setups, and rejections.\n\n"
    f"## Stage 2 Draft — Buckets 1 & 2\n{drafts[0]}\n\n"
    f"## Stage 2 Draft — Bucket 3\n{drafts[1]}\n"
)
critic_summary = await agent(
    critic_prompt,
    schema={"type": "string"},
    role="Risk Critic & Report Finalizer",
    type_name="self",
)
log("Risk critic summary:", critic_summary)

await run_cli(
    ["validate-report", "--report", str(report_path), "--candidates", str(candidates_path)],
    "Validate daily report against facts and invariants",
)
await run_cli(
    ["render-html", "--report", str(report_path), "--candidates", str(candidates_path)],
    "Render graphic HTML web dashboard for daily report",
)
await run_cli(
    ["record-ledger", "--report", str(report_path), "--candidates", str(candidates_path), "--date", run_date],
    "Record verified signals into SQLite ledger",
)
await run_cli(
    ["notify", "--report", str(report_path), *dry_run_args],
    "Dispatch daily mobile digest",
)

log(f"Daily scan complete: {report_path}")
