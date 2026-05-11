"""Per-tool unit tests over the parsed Volve cache. These run without the LLM —
    pytest tests/
should pass before any agent invocation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.tool_implementations import (  # noqa: E402
    answer_question,
    build_chart,
    classify_activities,
    compute_kpis,
    dispatch_tool,
    load_fleet_data,
    load_well_data,
)

HERO = "15/9-F-4"


def test_load_well_data_hero():
    d = load_well_data(HERO)
    assert "error" not in d
    assert d["well_id"] == HERO
    assert d["metadata"]["total_days"] > 0
    assert len(d["daily_reports"]) > 0


def test_load_well_data_missing_returns_error_dict():
    d = load_well_data("nonexistent-well-1234")
    assert d.get("error") == "well_not_found"
    assert "available" in d  # so the agent can suggest a near match


def test_classify_activities_tags_npt_subcategories():
    cd = classify_activities(load_well_data(HERO))
    cats = {a["category"] for r in cd["daily_reports"] for a in r["activities"]}
    assert {"drilling", "npt"}.issubset(cats)
    npt_subs = {
        a["npt_subcategory"]
        for r in cd["daily_reports"]
        for a in r["activities"]
        if a["category"] == "npt"
    }
    # F-4 is known to have fishing and weather NPT.
    assert "fishing" in npt_subs
    assert "weather" in npt_subs


def test_compute_kpis_hero_matches_known_story():
    kpis = compute_kpis(classify_activities(load_well_data(HERO)))
    # F-4: 107 actual days vs ~81 planned, ~25 days over.
    assert kpis["total_days"] == 107
    assert kpis["days_delta"] is not None and kpis["days_delta"] > 20
    # NPT around 25%.
    assert 20 < kpis["npt_percent"] < 30
    # Fishing should be among the top NPT subcategories.
    sub_top3 = [it["subcategory"] for it in kpis["npt_breakdown"][:3]]
    assert "fishing" in sub_top3
    # Anomalies should include at least one high-severity day.
    assert any(a["severity"] == "high" for a in kpis["top_anomalies"])


def test_build_chart_days_vs_depth():
    cd = classify_activities(load_well_data(HERO))
    spec = build_chart("days_vs_depth", cd)
    assert spec["chart_type"] == "days_vs_depth"
    assert len(spec["x"]) == len(spec["y"]) > 0
    assert spec["x_label"] == "Day" and spec["y_label"] == "MD (m)"


def test_build_chart_npt_pareto_from_kpis():
    kpis = compute_kpis(classify_activities(load_well_data(HERO)))
    spec = build_chart("npt_pareto", kpis)
    assert spec["chart_type"] == "npt_pareto"
    assert len(spec["labels"]) == len(spec["values"])


def test_build_chart_unknown_type_returns_error():
    spec = build_chart("definitely_not_a_chart", {})
    assert spec.get("error") == "unknown_chart_type"


def test_load_fleet_data_returns_all_wells():
    fleet = load_fleet_data()
    assert fleet["n_wells"] >= 24  # We parsed 26.
    sample = fleet["wells"][0]
    assert {"well_id", "npt_percent", "total_days", "td_md_m"}.issubset(sample)


def test_build_chart_fleet_npt_ranking_from_fleet():
    fleet = load_fleet_data()
    spec = build_chart("fleet_npt_ranking", fleet)
    assert spec["chart_type"] == "fleet_npt_ranking"
    # Sorted descending.
    assert spec["values"] == sorted(spec["values"], reverse=True)


def test_answer_question_well_scope_returns_kpis_and_worst_days():
    out = answer_question(scope="well", well_id=HERO, question="What was the worst day?")
    assert out["scope"] == "well"
    assert "kpis" in out
    assert len(out["worst_npt_days"]) == 5
    assert out["worst_npt_days"][0]["npt_hours"] >= out["worst_npt_days"][-1]["npt_hours"]


def test_answer_question_geology_is_out_of_scope():
    out = answer_question(scope="well", well_id=HERO, question="What is the porosity of the Hugin formation?")
    assert out.get("out_of_scope") is True


def test_dispatch_tool_unknown_returns_error():
    out = dispatch_tool("nope", {})
    assert out.get("error") == "unknown_tool"


def test_write_morning_memo_renders_template():
    from agent.tool_implementations import write_morning_memo

    kpis = compute_kpis(classify_activities(load_well_data(HERO)))
    out = write_morning_memo(
        well_id=HERO,
        kpis=kpis,
        anomalies=kpis["top_anomalies"][:3],
        charts=[],
        recommendation="Test recommendation.",
    )
    md = out["markdown"]
    assert HERO in md
    assert "{ACTUAL_DAYS}" not in md  # all placeholders filled
    assert "Test recommendation." in md
