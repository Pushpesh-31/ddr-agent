"""Plotly figure factories keyed by chart_type. The agent's `build_chart` tool returns
a JSON-serializable spec; this module converts the spec into a real `plotly.graph_objects.Figure`
that Streamlit can render via `st.plotly_chart`."""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go


def _empty_fig(message: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False, x=0.5, y=0.5, xref="paper", yref="paper")
    fig.update_layout(height=320, margin=dict(l=20, r=20, t=40, b=20))
    return fig


def days_vs_depth(spec: dict[str, Any]) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=spec.get("x", []),
        y=spec.get("y", []),
        mode="lines+markers",
        name="MD",
        line=dict(color="#0B5394", width=2),
        marker=dict(size=4),
    ))
    fig.update_layout(
        title=spec.get("title", "Days vs Depth"),
        xaxis_title=spec.get("x_label", "Day"),
        yaxis_title=spec.get("y_label", "MD (m)"),
        # Depth chart convention: deeper is lower on the page.
        yaxis=dict(autorange="reversed"),
        height=380,
        margin=dict(l=40, r=20, t=50, b=40),
    )
    return fig


def npt_pareto(spec: dict[str, Any]) -> go.Figure:
    labels = spec.get("labels", [])
    values = spec.get("values", [])
    fig = go.Figure(data=[go.Bar(
        x=labels,
        y=values,
        marker_color="#C0392B",
    )])
    fig.update_layout(
        title=spec.get("title", "NPT by Category"),
        xaxis_title="NPT category",
        yaxis_title=spec.get("value_label", "Days lost"),
        height=380,
        margin=dict(l=40, r=20, t=50, b=40),
    )
    return fig


def activity_breakdown(spec: dict[str, Any]) -> go.Figure:
    labels = spec.get("labels", [])
    values = spec.get("values", [])
    fig = go.Figure(data=[go.Pie(labels=labels, values=values, hole=0.45)])
    fig.update_layout(
        title=spec.get("title", "Time Allocation"),
        height=380,
        margin=dict(l=20, r=20, t=50, b=20),
    )
    return fig


def fleet_npt_ranking(spec: dict[str, Any]) -> go.Figure:
    labels = spec.get("labels", [])
    values = spec.get("values", [])
    fig = go.Figure(data=[go.Bar(
        y=labels[::-1],  # horizontal, biggest at top
        x=values[::-1],
        orientation="h",
        marker_color="#E67E22",
    )])
    fig.update_layout(
        title=spec.get("title", "Fleet NPT % by Well"),
        xaxis_title=spec.get("value_label", "NPT %"),
        height=max(320, 22 * len(labels) + 100),
        margin=dict(l=120, r=20, t=50, b=40),
    )
    return fig


_FACTORIES = {
    "days_vs_depth": days_vs_depth,
    "npt_pareto": npt_pareto,
    "activity_breakdown": activity_breakdown,
    "fleet_npt_ranking": fleet_npt_ranking,
}


def figure_for(spec: dict[str, Any]) -> go.Figure:
    """Dispatch a chart spec to the right factory. Returns a placeholder figure on unknown type."""
    ct = spec.get("chart_type")
    factory = _FACTORIES.get(ct)
    if factory is None:
        return _empty_fig(f"Unknown chart_type: {ct}")
    return factory(spec)
