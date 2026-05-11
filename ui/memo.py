"""Render the morning memo Markdown into Streamlit, swapping `{CHART:type}` markers
for Plotly figures from `ui/charts.py`."""

from __future__ import annotations

import re
from typing import Any

import streamlit as st

from ui.charts import figure_for

CHART_MARKER = re.compile(r"\{CHART:([a-z_]+)\}")


def render_memo(markdown: str, charts: list[dict[str, Any]]) -> None:
    """Split the memo on chart markers; render each segment as Markdown, each marker as a
    Plotly figure. Charts are matched to markers by `chart_type` — order doesn't matter."""
    by_type = {c.get("chart_type"): c for c in charts}
    cursor = 0
    for m in CHART_MARKER.finditer(markdown):
        before = markdown[cursor:m.start()]
        if before.strip():
            st.markdown(before)
        chart_type = m.group(1)
        spec = by_type.get(chart_type)
        if spec is not None:
            st.plotly_chart(figure_for(spec), use_container_width=True)
        else:
            st.info(f"_Chart not provided: `{chart_type}`_")
        cursor = m.end()
    tail = markdown[cursor:]
    if tail.strip():
        st.markdown(tail)


def word_count_body(markdown: str) -> int:
    """Per CLAUDE.md the memo body has a 200-word cap, excluding chart placeholders.
    Used for the soft warning banner in the UI."""
    stripped = CHART_MARKER.sub("", markdown)
    return len(stripped.split())
