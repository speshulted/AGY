"""Antigravity PreToolUse hook gating writes to reports/YYYY-MM-DD.md via deterministic validation."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.validate import validate_report_text  # noqa: E402

REPORT_NAME_RE = re.compile(r"reports[/\\](\d{4}-\d{2}-\d{2})\.md$", re.IGNORECASE)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        print(json.dumps({"decision": "allow"}))
        return 0

    tool_call = payload.get("toolCall", {})
    args = tool_call.get("args", {})
    target_path = str(args.get("Path", "") or args.get("path", ""))

    match = REPORT_NAME_RE.search(target_path)
    if not match:
        print(json.dumps({"decision": "allow"}))
        return 0

    run_date = match.group(1)
    candidates_path = PROJECT_ROOT / "data" / f"candidates_{run_date}.json"
    if not candidates_path.exists():
        print(
            json.dumps(
                {
                    "decision": "deny",
                    "reason": f"Cannot write {target_path}: missing Stage 1 candidates file {candidates_path}.",
                }
            )
        )
        return 0

    content = args.get("Content")
    if content is None:
        # For edit_file partial patches, allow and rely on post-write cli.py validate-report
        print(json.dumps({"decision": "allow"}))
        return 0

    candidates_payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    res = validate_report_text(str(content), candidates_payload)
    if not res.ok:
        print(
            json.dumps(
                {
                    "decision": "deny",
                    "reason": "Report failed deterministic fact-validator: " + "; ".join(res.errors[:5]),
                }
            )
        )
        return 0

    print(json.dumps({"decision": "allow"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
