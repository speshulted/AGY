"""Antigravity run_workflow orchestrator for Equity Pulse (Stage 1 -> Stage 2 -> Stage 3)."""

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
smoke_flag = " --smoke" if args.get("smoke", False) else ""
tickers_arg = f" --tickers {args['tickers']}" if args.get("tickers") else ""
dry_run_notify = " --dry-run" if args.get("dry_run_notify", False) else ""

candidates_path = repo_root / "data" / f"candidates_{run_date}.json"
report_path = repo_root / "reports" / f"{run_date}.md"

phase("Stage 1: Quantitative Grade, Ingest & Screen")
log(f"Running Stage 1 deterministic pipeline for {run_date} in {repo_root}")
await call_tool(
    "run_command",
    CommandLine=f"Set-Location '{repo_root}'; & '{python_exe}' -m src.cli grade-ledger --date {run_date}",
    WaitMsBeforeAsync=10000,
    toolSummary="Grade historical signal ledger",
)
await call_tool(
    "run_command",
    CommandLine=f"Set-Location '{repo_root}'; & '{python_exe}' -m src.cli ingest --date {run_date}{smoke_flag}{tickers_arg}",
    BypassSandbox=True,
    WaitMsBeforeAsync=10000,
    toolSummary="Ingest market, SEC EDGAR, and FINRA data",
)
await call_tool(
    "run_command",
    CommandLine=f"Set-Location '{repo_root}'; & '{python_exe}' -m src.cli screen --date {run_date}",
    WaitMsBeforeAsync=10000,
    toolSummary="Run deterministic 3-bucket screener",
)

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
    f"Drop weak, sector-inappropriate (e.g., bank Altman Z), or non-compliant setups, write the final verified report to `{report_path}`, "
    "and return a brief summary of surviving setups and rejections.\n\n"
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

await call_tool(
    "run_command",
    CommandLine=f"Set-Location '{repo_root}'; & '{python_exe}' -m src.cli validate-report --report '{report_path}' --candidates '{candidates_path}'",
    WaitMsBeforeAsync=10000,
    toolSummary="Validate daily report against facts and invariants",
)
await call_tool(
    "run_command",
    CommandLine=f"Set-Location '{repo_root}'; & '{python_exe}' -m src.cli record-ledger --report '{report_path}' --candidates '{candidates_path}' --date {run_date}",
    WaitMsBeforeAsync=10000,
    toolSummary="Record verified signals into SQLite ledger",
)
await call_tool(
    "run_command",
    CommandLine=f"Set-Location '{repo_root}'; & '{python_exe}' -m src.cli notify --report '{report_path}'{dry_run_notify}",
    WaitMsBeforeAsync=10000,
    toolSummary="Dispatch daily mobile digest",
)

log(f"Daily scan complete: {report_path}")
