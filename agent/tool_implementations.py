"""Implementations for the 7 tools in agent/tools.json.

Conventions (per CLAUDE.md):
- Pure functions over the parsed JSON cache. No network, no XML re-parsing.
- Return JSON-serializable dicts. On failure, return {"error": "<code>", ...} — do not raise.

Token-budget rule (added 2026-05-13):
- Tool *return values* are passed back into the model's message history on every loop
  iteration. Keep them small. Wells are ~140k tokens raw, so we pass `well_id` between
  tools and re-load the cache server-side via `_load_full_well` / `_classify_full`.
  The agent never sees the full daily-reports payload.
"""

from __future__ import annotations

import functools
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PARSED_DIR = Path(__file__).resolve().parent.parent / "data" / "parsed"
TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "morning_memo.md"


# ---------- Helpers ----------

def _well_filename(well_id: str) -> str:
    """`15/9-F-14` → `15_9-F-14.json`. Mirrors what parse_ddrs.py writes."""
    return well_id.replace("/", "_").replace(" ", "_") + ".json"


def _all_well_ids() -> list[str]:
    out = []
    for p in sorted(PARSED_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text())
            out.append(d["well_id"])
        except Exception:
            continue
    return out


# ---------- Activity classification ----------

# Map proprietaryCode (Volve's text-IADC) to (category, npt_subcategory_or_None).
# NPT subcategories follow the taxonomy in agent/system_prompt.md.

_CATEGORY_RULES: list[tuple[re.Pattern, str, str | None]] = [
    # NPT first — interruptions are always non-productive.
    (re.compile(r"^interruption\s*--\s*waiting on weather"), "npt", "weather"),
    (re.compile(r"^interruption\s*--\s*(wait|waiting)"), "npt", "waiting"),
    (re.compile(r"^interruption\s*--\s*fish"), "npt", "fishing"),
    (re.compile(r"^interruption\s*--\s*(repair|maintain)"), "npt", "mechanical"),
    (re.compile(r"^interruption\s*--\s*sidetrack"), "npt", "wellbore_instability"),
    (re.compile(r"^interruption\s*--\s*rig up/down"), "npt", "waiting"),
    (re.compile(r"^interruption"), "npt", "other"),
    # Productive activities.
    (re.compile(r"^drilling\s*--\s*casing"), "casing", None),
    (re.compile(r"^drilling\s*--\s*trip"), "tripping", None),
    (re.compile(r"^drilling\s*--\s*circulating"), "circulating", None),
    (re.compile(r"^drilling\s*--\s*(bop|bha)"), "bha_handling", None),
    (re.compile(r"^drilling\s*--\s*(drill|ream|survey|other)"), "drilling", None),
    (re.compile(r"^drilling"), "drilling", None),
    (re.compile(r"^completion\s*--\s*perforate"), "completion", None),
    (re.compile(r"^completion\s*--\s*completion string"), "completion", None),
    (re.compile(r"^completion\s*--\s*circulating"), "completion", None),
    (re.compile(r"^completion"), "completion", None),
    (re.compile(r"^formation evaluation\s*--\s*log"), "logging", None),
    (re.compile(r"^formation evaluation\s*--\s*drill stem test"), "testing", None),
    (re.compile(r"^formation evaluation"), "logging", None),
    (re.compile(r"^workover"), "workover", None),
    (re.compile(r"^plug abandon"), "plug_abandon", None),
    (re.compile(r"^moving"), "moving", None),
]

# Soft hints for refining `interruption -- other` (we already know it's NPT — just picking
# the best subcategory). Looser, can have false positives without harm.
_REMARK_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(lost circulation|losses|lcm|partial returns|total losses)\b", re.I), "lost_circulation"),
    (re.compile(r"\b(stuck pipe|pack[- ]?off|tight hole|swelling)\b", re.I), "wellbore_instability"),
    (re.compile(r"\b(kick|shut[- ]?in|bop test fail)\b", re.I), "well_control"),
    (re.compile(r"\b(twist[- ]?off|fish|junk|lost in hole|washover)\b", re.I), "fishing"),
    (re.compile(r"\b(top drive|surface equipment|motor failure|bha failure)\b", re.I), "mechanical"),
]

# High-precision hints for reclassifying a non-interruption activity (e.g., "drilling -- drill"
# coded but the 18-hour entry is actually a stuck-pipe event). Tighter patterns to keep the
# false-positive rate near zero — only flip if the language is unambiguous.
_NPT_STRONG_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(lost circulation|severe (mud )?losses|total losses|partial losses to formation)\b", re.I), "lost_circulation"),
    (re.compile(r"\b(stuck pipe|differentially stuck|pack[- ]?off (event|incident)|severe pack[- ]?off)\b", re.I), "wellbore_instability"),
    # Deliberately excluded: bare "shut-in well" — DST build-up, wireline lubrication, and
    # pressure-test ops all shut wells in routinely. Only flag unambiguous influx language.
    (re.compile(r"\b(took a kick|well kicked|swabbed in|kick (event|incident))\b", re.I), "well_control"),
    (re.compile(r"\b(twist[- ]?off|lost in hole|cut[- ]?and[- ]?thread fishing|fishing operation)\b", re.I), "fishing"),
    (re.compile(r"\b(top drive failure|tds failure|equipment failure|motor failure|bha failure)\b", re.I), "mechanical"),
]


def _classify_one(activity: dict) -> tuple[str, str | None]:
    code = (activity.get("code") or "").lower()
    remarks = activity.get("remarks") or ""
    hours = activity.get("hours") or 0
    cat, sub = "other", None
    for pat, c, s in _CATEGORY_RULES:
        if pat.match(code):
            cat, sub = c, s
            break
    if cat == "npt" and sub == "other":
        # Already NPT — just pick a better subcategory from remarks.
        for pat, refined in _REMARK_HINTS:
            if pat.search(remarks):
                sub = refined
                break
    elif cat != "npt" and hours >= 4:
        # Not coded as NPT, but the remarks describe a clear NPT event AND the activity is long
        # enough that misclassifying it would distort the KPIs. Use the high-precision patterns
        # to keep false positives down.
        for pat, refined in _NPT_STRONG_HINTS:
            if pat.search(remarks):
                cat, sub = "npt", refined
                break
    return cat, sub


# ---------- Server-side helpers (NOT exposed as tools) ----------
#
# These return the full classified well dict. Cached so a single agent run that calls
# classify_activities → compute_kpis → build_chart hits the disk + regex pass once,
# not three times. The cache is process-local and tiny (one entry per well).

@functools.cache
def _load_full_well(well_id: str) -> dict:
    path = PARSED_DIR / _well_filename(well_id)
    if not path.exists():
        return {"error": "well_not_found", "well_id": well_id, "available": _all_well_ids()}
    return json.loads(path.read_text())


@functools.cache
def _classify_full(well_id: str) -> dict:
    wd = _load_full_well(well_id)
    if "error" in wd:
        return wd
    out_dailies = []
    for r in wd["daily_reports"]:
        out_acts = []
        for a in r["activities"]:
            cat, sub = _classify_one(a)
            out_acts.append({**a, "category": cat, "npt_subcategory": sub})
        out_dailies.append({**r, "activities": out_acts})
    return {**wd, "daily_reports": out_dailies}


# ---------- Tools ----------

def load_well_data(well_id: str) -> dict:
    """Tool entry point. Returns a *small* summary — confirms the well exists and gives
    the agent enough metadata to orient. The full 100k+ token payload stays server-side."""
    wd = _load_full_well(well_id)
    if "error" in wd:
        return wd
    md = wd.get("metadata", {})
    dailies = wd.get("daily_reports", [])
    n_activities = sum(len(r.get("activities") or []) for r in dailies)
    first = dailies[0] if dailies else {}
    last = dailies[-1] if dailies else {}
    return {
        "well_id": wd["well_id"],
        "metadata": md,
        "n_daily_reports": len(dailies),
        "n_activities": n_activities,
        "date_range": {"start": first.get("date"), "end": last.get("date")},
        "first_day": {"day": first.get("day"), "date": first.get("date"), "depth_md_m": first.get("depth_md_end_m")},
        "last_day": {"day": last.get("day"), "date": last.get("date"), "depth_md_m": last.get("depth_md_end_m")},
    }


def load_fleet_data() -> dict:
    fleet: list[dict] = []
    for path in sorted(PARSED_DIR.glob("*.json")):
        d = json.loads(path.read_text())
        npt_hours = 0.0
        on_bottom_hours = 0.0
        total_logged_hours = 0.0
        notable: list[str] = []
        for r in d["daily_reports"]:
            for a in r["activities"]:
                hrs = a.get("hours") or 0
                total_logged_hours += hrs
                cat, _ = _classify_one(a)
                if cat == "npt":
                    npt_hours += hrs
                elif cat == "drilling":
                    on_bottom_hours += hrs
        denom_hours = (d["metadata"].get("total_days") or 1) * 24
        # Detect a couple of notable patterns for the summary.
        for r in d["daily_reports"]:
            day_npt = sum((a.get("hours") or 0) for a in r["activities"] if _classify_one(a)[0] == "npt")
            if day_npt >= 20:
                notable.append(f"Day {r['day']}: {day_npt:.0f}h NPT")
                if len(notable) >= 3:
                    break
        fleet.append({
            "well_id": d["well_id"],
            "operator": d["metadata"].get("operator"),
            "total_days": d["metadata"].get("total_days"),
            "planned_days": d["metadata"].get("planned_days"),
            "td_md_m": d["metadata"].get("td_md_m"),
            "npt_days": round(npt_hours / 24.0, 1),
            "npt_percent": round(100 * npt_hours / denom_hours, 1) if denom_hours else None,
            "on_bottom_percent": round(100 * on_bottom_hours / denom_hours, 1) if denom_hours else None,
            "notable_events": notable,
        })
    return {"wells": fleet, "n_wells": len(fleet)}


def classify_activities(well_id: str) -> dict:
    """Tool entry point. Classifies server-side and returns a *summary* of the result
    (counts by category/subcategory), not the full classified payload. Downstream tools
    call _classify_full internally via well_id."""
    cd = _classify_full(well_id)
    if "error" in cd:
        return cd
    cat_counts: Counter = Counter()
    sub_counts: Counter = Counter()
    cat_hours: dict[str, float] = defaultdict(float)
    for r in cd["daily_reports"]:
        for a in r["activities"]:
            cat = a.get("category") or "other"
            cat_counts[cat] += 1
            cat_hours[cat] += a.get("hours") or 0
            if cat == "npt":
                sub_counts[a.get("npt_subcategory") or "other"] += 1
    return {
        "well_id": well_id,
        "n_daily_reports": len(cd["daily_reports"]),
        "category_counts": dict(cat_counts),
        "category_hours": {k: round(v, 1) for k, v in cat_hours.items()},
        "npt_subcategory_counts": dict(sub_counts),
    }


def _sum_hours(activities: list[dict], category: str | None = None, subcategory: str | None = None) -> float:
    total = 0.0
    for a in activities:
        if category is not None and a.get("category") != category:
            continue
        if subcategory is not None and a.get("npt_subcategory") != subcategory:
            continue
        total += a.get("hours") or 0
    return total


def compute_kpis(well_id: str) -> dict:
    """Tool entry point. Loads + classifies server-side from well_id, returns the existing
    small KPIs dict (~1-2 KB). The agent does not need to thread classified data through."""
    cd = _classify_full(well_id)
    if "error" in cd:
        return cd
    md = cd["metadata"]
    dailies = cd["daily_reports"]

    npt_hours = _sum_hours([a for r in dailies for a in r["activities"]], category="npt")
    on_bottom_hours = _sum_hours([a for r in dailies for a in r["activities"]], category="drilling")
    total_hours = (md.get("total_days") or 1) * 24

    # NPT by subcategory
    npt_by_sub: dict[str, float] = defaultdict(float)
    for r in dailies:
        for a in r["activities"]:
            if a.get("category") == "npt":
                npt_by_sub[a.get("npt_subcategory") or "other"] += a.get("hours") or 0
    npt_breakdown = sorted(
        [{"subcategory": k, "hours": round(v, 1), "days": round(v / 24.0, 1)} for k, v in npt_by_sub.items()],
        key=lambda x: -x["hours"],
    )

    # Anomalies: high-NPT days, depth stagnation, long single stops.
    anomalies: list[dict] = []
    prev_md = None
    for r in dailies:
        day_npt = sum((a.get("hours") or 0) for a in r["activities"] if a.get("category") == "npt")
        long_stop = max(((a.get("hours") or 0) for a in r["activities"] if a.get("category") == "npt"), default=0)
        if day_npt >= 12:
            top_sub_pair = Counter(
                a.get("npt_subcategory") for a in r["activities"] if a.get("category") == "npt"
            ).most_common(1)
            top_sub = top_sub_pair[0][0] if top_sub_pair else None
            anomalies.append({
                "day": r["day"],
                "date": r["date"],
                "depth_md_m": r.get("depth_md_end_m"),
                "description": f"{day_npt:.1f}h NPT" + (f" ({top_sub})" if top_sub else ""),
                "top_subcategory": top_sub,
                "severity": "high" if day_npt >= 20 else "medium",
                "remarks": (r.get("remarks_combined") or "")[:240],
            })
        elif long_stop >= 12:
            # Find the subcategory of the long stop itself.
            long_a = max((a for a in r["activities"] if a.get("category") == "npt"), key=lambda a: a.get("hours") or 0, default=None)
            anomalies.append({
                "day": r["day"],
                "date": r["date"],
                "depth_md_m": r.get("depth_md_end_m"),
                "description": f"single stop {long_stop:.1f}h",
                "top_subcategory": long_a.get("npt_subcategory") if long_a else None,
                "severity": "medium",
                "remarks": (r.get("remarks_combined") or "")[:240],
            })
        elif prev_md is not None and r.get("depth_md_end_m") is not None and prev_md == r["depth_md_end_m"] and day_npt < 4:
            # Stagnation without explicit NPT — drilling supt should look at this.
            pass  # too noisy to surface every flat day; skip
        prev_md = r.get("depth_md_end_m") if r.get("depth_md_end_m") is not None else prev_md

    # Sort anomalies by severity then day.
    anomalies.sort(key=lambda x: (-1 if x["severity"] == "high" else 0, x["day"]))

    return {
        "well_id": cd["well_id"],
        "total_days": md.get("total_days"),
        "planned_days": md.get("planned_days"),
        "days_delta": round((md.get("total_days") or 0) - (md.get("planned_days") or 0), 1) if md.get("planned_days") else None,
        "calendar_span_days": md.get("calendar_span_days"),
        "td_md_m": md.get("td_md_m"),
        "td_tvd_m": md.get("td_tvd_m"),
        "npt_total_hours": round(npt_hours, 1),
        "npt_total_days": round(npt_hours / 24.0, 1),
        "npt_percent": round(100 * npt_hours / total_hours, 1),
        "on_bottom_percent": round(100 * on_bottom_hours / total_hours, 1),
        "npt_breakdown": npt_breakdown,
        "top_anomalies": anomalies[:8],
    }


def build_chart(chart_type: str, well_id: str | None = None, kpis: dict | None = None) -> dict:
    """Return a small chart spec the UI's `ui/charts.py` knows how to render with Plotly.

    Inputs are kept small: pass `well_id` for charts that need per-day data (the function
    loads + classifies server-side), or pass the already-small `kpis` dict for npt_pareto.
    `fleet_npt_ranking` loads the fleet internally and needs no input."""
    if chart_type == "days_vs_depth":
        if not well_id:
            return {"error": "missing_well_id", "chart_type": chart_type}
        cd = _classify_full(well_id)
        if "error" in cd:
            return cd
        xs = [r["day"] for r in cd["daily_reports"]]
        ys = [r.get("depth_md_end_m") for r in cd["daily_reports"]]
        return {
            "chart_type": chart_type,
            "title": f"Days vs Depth — {cd.get('well_id', '')}",
            "x": xs, "y": ys, "x_label": "Day", "y_label": "MD (m)",
        }

    if chart_type == "npt_pareto":
        # Prefer the passed-in kpis (cheaper than re-classifying); fall back to well_id.
        if kpis and "npt_breakdown" in kpis:
            items = kpis["npt_breakdown"]
            title = f"NPT by Category — {kpis.get('well_id', '')}"
        elif well_id:
            k = compute_kpis(well_id)
            if "error" in k:
                return k
            items = k["npt_breakdown"]
            title = f"NPT by Category — {well_id}"
        else:
            return {"error": "missing_input", "chart_type": chart_type, "expected": "well_id or kpis"}
        labels = [it["subcategory"] for it in items]
        values = [it["days"] for it in items]
        return {"chart_type": chart_type, "title": title, "labels": labels, "values": values, "value_label": "Days lost"}

    if chart_type == "activity_breakdown":
        if not well_id:
            return {"error": "missing_well_id", "chart_type": chart_type}
        cd = _classify_full(well_id)
        if "error" in cd:
            return cd
        totals: dict[str, float] = defaultdict(float)
        for r in cd["daily_reports"]:
            for a in r["activities"]:
                totals[a.get("category") or "other"] += (a.get("hours") or 0) / 24.0
        items = sorted(totals.items(), key=lambda kv: -kv[1])
        return {
            "chart_type": chart_type,
            "title": f"Time Allocation — {cd.get('well_id', '')}",
            "labels": [k for k, _ in items],
            "values": [round(v, 2) for _, v in items],
            "value_label": "Days",
        }

    if chart_type == "fleet_npt_ranking":
        fleet = load_fleet_data()
        wells = sorted(fleet["wells"], key=lambda w: -(w.get("npt_percent") or 0))
        return {
            "chart_type": chart_type,
            "title": "Fleet NPT % by well",
            "labels": [w["well_id"] for w in wells],
            "values": [w.get("npt_percent") or 0 for w in wells],
            "value_label": "NPT %",
        }

    return {"error": "unknown_chart_type", "chart_type": chart_type}


def write_morning_memo(
    well_id: str,
    kpis: dict,
    anomalies: list[dict] | None = None,
    charts: list[dict] | None = None,
    recommendation: str = "",
) -> dict:
    """Fill the markdown template. Returns the rendered string and the chart specs the UI
    should render in place of the {CHART:type} markers."""
    template = _load_template()
    n_ddrs = kpis.get("total_days") or 0
    delta = kpis.get("days_delta")
    delta_str = f"{delta:+.1f}" if isinstance(delta, (int, float)) else "n/a"

    # Top three NPT drivers
    breakdown = kpis.get("npt_breakdown") or []
    npt_total_days = kpis.get("npt_total_days") or 0

    def slot(i: int, key: str, default: str = "—") -> str:
        if i >= len(breakdown):
            return default
        it = breakdown[i]
        if key == "cat":
            return it.get("subcategory") or "other"
        if key == "days":
            return f"{it.get('days', 0):.1f}"
        if key == "pct":
            return f"{(100 * it.get('days', 0) / npt_total_days):.0f}" if npt_total_days else "—"
        return default

    anomalies = anomalies or kpis.get("top_anomalies") or []
    def anom_slot(i: int, key: str, default: str = "—") -> str:
        if i >= len(anomalies):
            return default
        a = anomalies[i]
        if key == "day":
            return str(a.get("day", "—"))
        if key == "desc":
            return a.get("description") or (a.get("remarks") or "")[:80]
        if key == "depth":
            d = a.get("depth_md_m")
            return f"{d:.0f}" if d is not None else "—"
        if key == "duration":
            return a.get("description") or "—"
        return default

    # Build a subcategory → first-anomaly-remarks index from the FULL anomaly list
    # (kpis.top_anomalies) rather than just the slice passed in for Notable Events.
    # That's the bug fix: previously, weather (the largest NPT bucket on F-4) showed "—"
    # because no Notable Event in the top-3 slice had "weather" in its description.
    full_anomalies = kpis.get("top_anomalies") or anomalies
    by_subcat: dict[str, str] = {}
    for a in full_anomalies:
        sub = a.get("top_subcategory")
        if sub and sub not in by_subcat:
            by_subcat[sub] = (a.get("remarks") or "—")[:120]

    def root_cause(i: int) -> str:
        if i >= len(breakdown):
            return "—"
        sub = breakdown[i].get("subcategory")
        return by_subcat.get(sub, "—")

    from datetime import date as _date
    fields = {
        "WELL_ID": well_id,
        "DATE": _date.today().isoformat(),
        "N_DDRS": str(n_ddrs),
        "ONE_LINE_PUNCHLINE": _build_punchline(kpis),
        "ACTUAL_DAYS": str(kpis.get("total_days", "—")),
        "PLANNED_DAYS": str(kpis.get("planned_days", "—")),
        "DELTA_SIGNED": delta_str,
        "TD_MD": str(kpis.get("td_md_m", "—")),
        "TD_TVD": str(kpis.get("td_tvd_m", "—")),
        "NPT_PERCENT": str(kpis.get("npt_percent", "—")),
        "NPT_DAYS": str(npt_total_days),
        "ON_BOTTOM_PERCENT": str(kpis.get("on_bottom_percent", "—")),
        "NPT_CAT_1": slot(0, "cat"), "NPT_DAYS_1": slot(0, "days"), "NPT_PCT_1": slot(0, "pct"),
        "NPT_CAT_2": slot(1, "cat"), "NPT_DAYS_2": slot(1, "days"), "NPT_PCT_2": slot(1, "pct"),
        "NPT_CAT_3": slot(2, "cat"), "NPT_DAYS_3": slot(2, "days"), "NPT_PCT_3": slot(2, "pct"),
        "ROOT_CAUSE_1": root_cause(0), "ROOT_CAUSE_2": root_cause(1), "ROOT_CAUSE_3": root_cause(2),
        "DAY_A": anom_slot(0, "day"), "EVENT_A_DESC": anom_slot(0, "desc"),
        "DEPTH_A": anom_slot(0, "depth"), "DURATION_A": anom_slot(0, "duration"),
        "DAY_B": anom_slot(1, "day"), "EVENT_B_DESC": anom_slot(1, "desc"),
        "DEPTH_B": anom_slot(1, "depth"), "DURATION_B": anom_slot(1, "duration"),
        "DAY_C": anom_slot(2, "day"), "EVENT_C_DESC": anom_slot(2, "desc"),
        "DEPTH_C": anom_slot(2, "depth"), "DURATION_C": anom_slot(2, "duration"),
        "RECOMMENDATION": recommendation or "—",
        "SOURCE_FILES": f"data/parsed/{_well_filename(well_id)}",
    }
    rendered = template
    for k, v in fields.items():
        rendered = rendered.replace("{" + k + "}", v)
    return {"markdown": rendered, "charts": charts or []}


def _build_punchline(kpis: dict) -> str:
    delta = kpis.get("days_delta")
    npt_pct = kpis.get("npt_percent")
    breakdown = kpis.get("npt_breakdown") or []
    top_npt = breakdown[0]["subcategory"] if breakdown else "n/a"
    direction = "over" if isinstance(delta, (int, float)) and delta > 0 else "under"
    parts = []
    if isinstance(delta, (int, float)):
        parts.append(f"{abs(delta):.1f} days {direction} plan")
    if npt_pct is not None:
        parts.append(f"NPT {npt_pct}% (largest bucket: {top_npt})")
    return ". ".join(parts) + "." if parts else "Triage memo."


_TEMPLATE_CACHE: str | None = None


def _load_template() -> str:
    """Extract the first fenced ```markdown ... ``` block from templates/morning_memo.md."""
    global _TEMPLATE_CACHE
    if _TEMPLATE_CACHE is not None:
        return _TEMPLATE_CACHE
    text = TEMPLATE_PATH.read_text()
    m = re.search(r"```markdown\n(.*?)\n```", text, re.DOTALL)
    if not m:
        # Fall back to the whole file if there's no fence.
        _TEMPLATE_CACHE = text
    else:
        _TEMPLATE_CACHE = m.group(1)
    return _TEMPLATE_CACHE


_OUT_OF_SCOPE_PAT = re.compile(
    r"\b(geology|formation top|reservoir|porosity|permeability|completion design|completion strategy|petrophysics|seismic)\b",
    re.I,
)


def answer_question(scope: str, question: str, well_id: str | None = None) -> dict:
    """Return data slices for the agent to synthesize. Sets out_of_scope=True for non-DDR
    questions so the agent can say so cleanly."""
    if _OUT_OF_SCOPE_PAT.search(question):
        return {"out_of_scope": True, "reason": "Question is about geology/reservoir/completions design — not in DDR scope.", "question": question}

    if scope == "well":
        if not well_id:
            return {"error": "missing_well_id", "scope": scope}
        cd = _classify_full(well_id)
        if "error" in cd:
            return cd
        kpis = compute_kpis(well_id)
        # Pre-aggregate a few useful slices.
        worst_days = sorted(
            [
                {
                    "day": r["day"], "date": r["date"], "depth_md_m": r.get("depth_md_end_m"),
                    "npt_hours": round(sum((a.get("hours") or 0) for a in r["activities"] if a.get("category") == "npt"), 1),
                    "remarks": (r.get("remarks_combined") or "")[:300],
                }
                for r in cd["daily_reports"]
            ],
            key=lambda x: -x["npt_hours"],
        )[:5]
        return {
            "scope": "well", "well_id": well_id, "question": question,
            "kpis": kpis,
            "worst_npt_days": worst_days,
            "metadata": cd["metadata"],
        }

    if scope == "fleet":
        fleet = load_fleet_data()
        avg_npt = round(sum((w.get("npt_percent") or 0) for w in fleet["wells"]) / max(len(fleet["wells"]), 1), 1)
        return {
            "scope": "fleet", "question": question,
            "fleet": fleet,
            "fleet_avg_npt_percent": avg_npt,
        }

    return {"error": "bad_scope", "scope": scope, "expected": ["well", "fleet"]}


# ---------- Dispatch ----------

TOOL_REGISTRY = {
    "load_well_data": load_well_data,
    "load_fleet_data": load_fleet_data,
    "classify_activities": classify_activities,
    "compute_kpis": compute_kpis,
    "build_chart": build_chart,
    "write_morning_memo": write_morning_memo,
    "answer_question": answer_question,
}


def dispatch_tool(name: str, input_dict: dict[str, Any]) -> dict:
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return {"error": "unknown_tool", "name": name, "available": list(TOOL_REGISTRY)}
    return fn(**input_dict)
