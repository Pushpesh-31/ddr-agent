"""Build a deterministic cached run for the demo-mode safety net.

This bypasses the LLM entirely — it calls the same tool functions the agent would call,
in the same order, and writes the result to data/cached_runs/. The cached run is what
the Streamlit app loads when "Demo mode (cached)" is checked.

Run once before the demo (and re-run if the parsed cache changes):
    python scripts/build_cached_run.py --well 15/9-F-4
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.tool_implementations import (  # noqa: E402
    build_chart,
    classify_activities,
    compute_kpis,
    load_well_data,
    write_morning_memo,
)

CACHED_DIR = Path(__file__).resolve().parent.parent / "data" / "cached_runs"


def build_for_well(well_id: str, recommendation: str) -> dict:
    summary = load_well_data(well_id)
    if "error" in summary:
        raise SystemExit(f"Well not found: {well_id}")
    cls = classify_activities(well_id)
    kpis = compute_kpis(well_id)
    chart_dvd = build_chart("days_vs_depth", well_id=well_id)
    chart_pareto = build_chart("npt_pareto", kpis=kpis)
    memo = write_morning_memo(
        well_id=well_id,
        recommendation=recommendation,
        charts=[chart_dvd, chart_pareto],
        kpis=kpis,
        anomalies=kpis["top_anomalies"][:3],
    )
    return {
        "well_id": well_id,
        "memo": memo["markdown"],
        "charts": [chart_dvd, chart_pareto],
        "log": [
            f"→ load_well_data({{\"well_id\": \"{well_id}\"}})",
            f"← load_well_data: {summary['n_daily_reports']} daily reports",
            f"→ classify_activities({{\"well_id\": \"{well_id}\"}})",
            f"← classify_activities: {cls['category_counts'].get('npt', 0)} NPT activities tagged",
            f"→ compute_kpis({{\"well_id\": \"{well_id}\"}})",
            f"← compute_kpis: {kpis['npt_percent']}% NPT",
            f"→ build_chart({{\"chart_type\": \"days_vs_depth\", \"well_id\": \"{well_id}\"}})",
            f"← build_chart: days_vs_depth",
            f"→ build_chart({{\"chart_type\": \"npt_pareto\", \"kpis\": <...>}})",
            f"← build_chart: npt_pareto",
            f"→ write_morning_memo(<...>)",
            f"← write_morning_memo: rendered",
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--well", default="15/9-F-4", help="Well to cache (the hero well)")
    ap.add_argument(
        "--recommendation",
        default=(
            "On the next well in this template, budget contingency for MDT fishing — "
            "F-4's stuck-tool event at 3,245 m cost 5.7 days. Pre-stage cut-and-thread "
            "fishing kit and brief crew on the procedure before running MDT on wireline "
            "below 3,000 m."
        ),
    )
    args = ap.parse_args()
    CACHED_DIR.mkdir(parents=True, exist_ok=True)
    result = build_for_well(args.well, args.recommendation)
    safe = args.well.replace("/", "_").replace(" ", "_")
    out = CACHED_DIR / f"triage_{safe}.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"Wrote {out}")
    print(f"  memo: {len(result['memo'])} chars  charts: {len(result['charts'])}")


if __name__ == "__main__":
    main()
