"""Render the agent's tool-use trace into a Streamlit container so the user can watch
the agent plan and call tools live. This is what makes the demo feel agentic instead of
a single-shot summarizer."""

from __future__ import annotations

import json

import streamlit as st

from agent.llm_client import EndTurn, TextDelta, ToolResult, ToolUse


class ActivityLog:
    """A small wrapper around a Streamlit container that appends a line per event.
    Re-create on each agent run; do not reuse across reruns."""

    def __init__(self, container, *, show_results: bool = True):
        self.container = container
        self.show_results = show_results
        self._n = 0

    def push(self, ev) -> None:
        self._n += 1
        if isinstance(ev, ToolUse):
            preview = json.dumps(ev.input, ensure_ascii=False)
            if len(preview) > 140:
                preview = preview[:137] + "..."
            self.container.markdown(f"**→ `{ev.name}`**  \n`{preview}`")
        elif isinstance(ev, ToolResult) and self.show_results:
            mark = "❗" if ev.is_error else "✓"
            summary = _summarize(ev.output)
            self.container.markdown(f"{mark} `{ev.name}` — {summary}")
        elif isinstance(ev, EndTurn):
            if ev.stop_reason == "max_turns_exceeded":
                self.container.warning("Max turns exceeded — agent stopped without producing a final answer.")
            elif ev.stop_reason != "end_turn":
                self.container.caption(f"_stop: {ev.stop_reason}_")
        elif isinstance(ev, TextDelta):
            # Text deltas are routed to the memo placeholder, not the activity log.
            pass


def _summarize(output) -> str:
    """Compress a tool result to one line for the activity-log feed."""
    if isinstance(output, dict):
        if "error" in output:
            return f"error: {output['error']}"
        if "well_id" in output and "daily_reports" in output:
            return f"loaded {output['well_id']} ({len(output['daily_reports'])} reports)"
        if "wells" in output:
            return f"fleet ({output.get('n_wells', len(output['wells']))} wells)"
        if "npt_breakdown" in output:
            return f"kpis: {output.get('npt_percent', '?')}% NPT, {len(output.get('top_anomalies', []))} anomalies"
        if "chart_type" in output:
            return f"chart spec: {output['chart_type']}"
        if "markdown" in output:
            return f"memo rendered ({len(output['markdown'])} chars)"
        if "worst_npt_days" in output:
            return f"qa slice ({len(output['worst_npt_days'])} worst days)"
        return "result"
    return str(output)[:80]
