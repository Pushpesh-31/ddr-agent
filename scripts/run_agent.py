"""CLI smoke test for the agent — no Streamlit required.

    python scripts/run_agent.py --well 15/9-F-4 --mode triage
    python scripts/run_agent.py --well 15/9-F-4 --mode qa --question "What was the worst day on this well?"
    python scripts/run_agent.py --mode fleet --question "Rank wells by NPT %"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow `python scripts/run_agent.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.llm_client import EndTurn, TextDelta, ToolResult, ToolUse  # noqa: E402
from agent.runner import run_agent  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["triage", "qa", "fleet"], default="triage")
    ap.add_argument("--well", help="Well ID, e.g., 15/9-F-4")
    ap.add_argument("--question", default="", help="Free-text question for qa or fleet mode")
    args = ap.parse_args()

    if args.mode == "triage":
        if not args.well:
            ap.error("--well is required for triage mode")
        prompt = f"Triage well {args.well}. Produce the morning memo per your standard workflow."
    elif args.mode == "qa":
        if not args.well:
            ap.error("--well is required for qa mode")
        if not args.question:
            ap.error("--question is required for qa mode")
        prompt = f"Question about well {args.well}: {args.question}"
    else:  # fleet
        if not args.question:
            ap.error("--question is required for fleet mode")
        prompt = f"Fleet question: {args.question}"

    print(f"\n>>> {prompt}\n", flush=True)
    text_buf: list[str] = []
    for ev in run_agent(prompt, mode=args.mode):
        if isinstance(ev, TextDelta):
            print(ev.text, end="", flush=True)
            text_buf.append(ev.text)
        elif isinstance(ev, ToolUse):
            preview = json.dumps(ev.input)[:120]
            print(f"\n  [tool] {ev.name}({preview})", flush=True)
        elif isinstance(ev, ToolResult):
            mark = "ERR" if ev.is_error else "ok"
            print(f"  [{mark}] {ev.name}", flush=True)
        elif isinstance(ev, EndTurn):
            if ev.stop_reason in ("end_turn", "max_turns_exceeded"):
                print(f"\n--- {ev.stop_reason} ---", flush=True)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
