"""DDR Triage Agent — Streamlit entrypoint.

Run locally:
    streamlit run app.py

Deploy: push to GitHub, connect to Streamlit Community Cloud, set ANTHROPIC_API_KEY in
the app's Secrets dashboard.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from agent.llm_client import EndTurn, TextDelta, ToolResult, ToolUse
from agent.runner import run_agent
from agent.tool_implementations import _all_well_ids
from ui.activity_log import ActivityLog
from ui.memo import render_memo, word_count_body

CACHED_DIR = Path(__file__).resolve().parent / "data" / "cached_runs"
HERO_WELL = "15/9-F-4"

st.set_page_config(page_title="DDR Triage Agent", layout="wide", page_icon="🛢️")


# ---------- Session state ----------

def _init_state() -> None:
    st.session_state.setdefault("triage_result", None)         # {"memo": str, "charts": list, "log": list}
    st.session_state.setdefault("qa_history", [])              # list of {role, content}
    st.session_state.setdefault("fleet_result", None)


_init_state()


# ---------- Sidebar ----------

with st.sidebar:
    st.markdown("### DDR Triage Agent")
    st.caption("Volve field — 26 wells, 1,380 DDRs")

    mode = st.radio(
        "Mode",
        options=["Triage", "Q&A", "Fleet"],
        index=0,
        help="Triage produces a morning memo. Q&A answers free-text questions about one well. "
             "Fleet ranks/compares across all wells.",
    )

    wells = _all_well_ids() or [HERO_WELL]
    default_index = wells.index(HERO_WELL) if HERO_WELL in wells else 0
    well_id = st.selectbox(
        "Well",
        options=wells,
        index=default_index,
        disabled=(mode == "Fleet"),
        help="Selected well for Triage and Q&A. Fleet mode runs across all wells.",
    )

    demo_mode = st.checkbox(
        "Demo mode (cached)",
        value=False,
        help="Replay a pre-recorded clean run instead of calling the live API. "
             "Use as the live-demo safety net when the network or API is unreliable.",
    )

    st.divider()
    st.caption("Powered by **claude-sonnet-4-6** via the Anthropic Python SDK. "
               "Provider abstracted — see `agent/llm_client.py`.")


# ---------- Helpers ----------

def _cached_run(mode_key: str, well_id: str | None) -> dict | None:
    """Load a recorded run from data/cached_runs/. Returns None if not present."""
    if not CACHED_DIR.exists():
        return None
    name = f"{mode_key}_{(well_id or 'fleet').replace('/', '_').replace(' ', '_')}.json"
    path = CACHED_DIR / name
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _stream_agent_into_ui(
    prompt: str,
    mode_str: str,
    *,
    memo_placeholder=None,
    log_container=None,
) -> tuple[str, list[dict], list[str]]:
    """Run the agent generator and route events to the UI in real time. Returns the
    final memo Markdown, the chart specs collected, and the activity log lines."""
    log = ActivityLog(log_container) if log_container is not None else None
    text_buf: list[str] = []
    chart_specs: list[dict] = []
    log_lines: list[str] = []

    for ev in run_agent(prompt, mode=mode_str):
        if isinstance(ev, TextDelta):
            text_buf.append(ev.text)
            if memo_placeholder is not None:
                memo_placeholder.markdown("".join(text_buf))
        elif isinstance(ev, ToolUse):
            log_lines.append(f"→ {ev.name}({json.dumps(ev.input)[:80]})")
            if log:
                log.push(ev)
        elif isinstance(ev, ToolResult):
            log_lines.append(f"← {ev.name}: {'error' if ev.is_error else 'ok'}")
            if log:
                log.push(ev)
            # Capture chart specs and memo output for re-rendering.
            if ev.name == "build_chart" and isinstance(ev.output, dict) and "error" not in ev.output:
                chart_specs.append(ev.output)
            elif ev.name == "write_morning_memo" and isinstance(ev.output, dict):
                if md := ev.output.get("markdown"):
                    text_buf = [md]
                    if memo_placeholder is not None:
                        memo_placeholder.markdown(md)
                for c in ev.output.get("charts") or []:
                    chart_specs.append(c)
        elif isinstance(ev, EndTurn):
            if log:
                log.push(ev)

    return "".join(text_buf), chart_specs, log_lines


# ---------- Main panel ----------

st.title(f"{mode} — {well_id if mode != 'Fleet' else 'Fleet'}")

if mode == "Triage":
    st.write("Generate the morning triage memo for the selected well. The agent will load "
             "DDRs, classify activities, surface NPT, and assemble the memo.")

    if st.button("Run triage", type="primary"):
        st.session_state.triage_result = None
        memo_col, log_col = st.columns([3, 2])
        with memo_col:
            st.subheader("Memo")
            memo_placeholder = st.empty()
        with log_col:
            with st.expander("Agent thinking", expanded=True):
                log_container = st.container()

        if demo_mode:
            cached = _cached_run("triage", well_id)
            if cached is None:
                st.warning(f"No cached run for {well_id} yet. Disable demo mode and run live to record one.")
            else:
                memo_placeholder.markdown(cached.get("memo", ""))
                st.session_state.triage_result = cached
        else:
            prompt = f"Triage well {well_id}. Produce the morning memo per your standard workflow."
            try:
                memo_md, charts, log_lines = _stream_agent_into_ui(
                    prompt, "triage",
                    memo_placeholder=memo_placeholder,
                    log_container=log_container,
                )
                st.session_state.triage_result = {"memo": memo_md, "charts": charts, "log": log_lines}
                # Soft warning if memo body is over the 200-word cap.
                wc = word_count_body(memo_md)
                if wc > 220:
                    st.caption(f"_memo body: {wc} words (target ≤ 200)_")
            except Exception as exc:
                st.error(f"Agent error: {exc}")

    # Re-render saved result on subsequent interactions.
    res = st.session_state.triage_result
    if res and not st.session_state.get("_just_rendered_triage"):
        # The button branch already rendered into the placeholders above; on a rerun
        # without a button click, re-render from scratch using render_memo so charts
        # land in the right places.
        st.subheader("Memo")
        render_memo(res["memo"], res.get("charts") or [])
        if res.get("log"):
            with st.expander("Agent thinking (last run)", expanded=False):
                for line in res["log"]:
                    st.text(line)


elif mode == "Q&A":
    st.write(f"Ask a free-text question about **{well_id}**. The agent will reason over the parsed DDRs.")
    for msg in st.session_state.qa_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if user_q := st.chat_input("e.g., What was the worst day on this well?"):
        st.session_state.qa_history.append({"role": "user", "content": user_q})
        with st.chat_message("user"):
            st.markdown(user_q)
        with st.chat_message("assistant"):
            answer_placeholder = st.empty()
            with st.expander("Agent thinking", expanded=False):
                log_container = st.container()
            prompt = f"Question about well {well_id}: {user_q}"
            try:
                answer, _, _ = _stream_agent_into_ui(
                    prompt, "qa",
                    memo_placeholder=answer_placeholder,
                    log_container=log_container,
                )
                st.session_state.qa_history.append({"role": "assistant", "content": answer})
            except Exception as exc:
                st.error(f"Agent error: {exc}")


else:  # Fleet
    st.write("Ask a question across all 26 wells in the Volve fleet. Examples: "
             "*Rank wells by fishing NPT %.* / *Which wells came in over plan?*")
    user_q = st.text_input("Fleet question", value="Rank wells by NPT %.")
    if st.button("Ask fleet", type="primary"):
        memo_col, log_col = st.columns([3, 2])
        with memo_col:
            st.subheader("Answer")
            answer_placeholder = st.empty()
        with log_col:
            with st.expander("Agent thinking", expanded=True):
                log_container = st.container()
        prompt = f"Fleet question: {user_q}"
        try:
            answer, charts, log_lines = _stream_agent_into_ui(
                prompt, "fleet",
                memo_placeholder=answer_placeholder,
                log_container=log_container,
            )
            st.session_state.fleet_result = {"memo": answer, "charts": charts, "log": log_lines}
        except Exception as exc:
            st.error(f"Agent error: {exc}")

    res = st.session_state.fleet_result
    if res and res.get("charts"):
        for spec in res["charts"]:
            from ui.charts import figure_for
            st.plotly_chart(figure_for(spec), use_container_width=True)
