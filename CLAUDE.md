# DDR Triage Agent

An agentic workflow that ingests daily drilling reports (DDRs), classifies activities, surfaces non-productive time (NPT) by category, and produces a morning triage memo plus an interactive Q&A surface. Built on the Anthropic SDK tool-use loop, fronted by a Streamlit UI. Designed to be extended — adding new tools, new modes, or new output formats should each be a 1-2 hour change following the recipes in this doc.

This repo is also a teaching artifact (shared with students at the SPE young-professionals webinar). Keep the surface area small and the agent loop legible.

---

## Stack

- **UI:** Streamlit (single-page app, `app.py` at repo root)
- **Charts:** Plotly (rendered via `st.plotly_chart`)
- **Agent:** Anthropic Python SDK, `claude-sonnet-4-6` model, tool-use loop — accessed through an `LLMClient` abstraction so the model provider can be swapped (Gemini free tier is the planned alternate; not yet implemented)
- **Data:** Volve DDR XML → parsed JSON cache (committed to the repo)
- **Runtime:** Python 3.11+
- **Deploy:** Streamlit Community Cloud (free, GitHub-integrated). Secrets handled via the Streamlit Secrets dashboard.

---

## Project structure

```
ddr-agent/
├── data/
│   ├── raw/                  # Volve DDR XMLs (gitignored, ~24 wells)
│   └── parsed/               # JSON cache, one file per well (committed)
├── scripts/
│   ├── parse_ddrs.py         # XML → JSON, run once at build time
│   └── run_agent.py          # CLI smoke test for the agent (no Streamlit)
├── agent/
│   ├── system_prompt.md      # Loaded as the `system` param in API calls
│   ├── tools.json            # Tool schemas (Anthropic format — canonical)
│   ├── llm_client.py         # Abstract LLMClient + AnthropicClient (swap point for Gemini)
│   ├── runner.py             # Tool-use loop, generator-based for Streamlit streaming
│   └── tool_implementations.py  # One function per tool, dispatched by name
├── templates/
│   └── morning_memo.md       # Markdown template the agent fills
├── ui/
│   ├── charts.py             # Plotly figures keyed by chart_type
│   ├── memo.py               # Renders memo Markdown, swaps {CHART:type} for plotly figures
│   └── activity_log.py       # Streams tool_use events into a Streamlit expander
├── docs/
│   ├── DEMO_PLAN.md          # Webinar talk track and sprint plan
│   └── hero_well.md          # Ground-truth story for the hero well (eval rubric)
├── tests/
│   └── test_tools.py         # Unit tests per tool function
├── .streamlit/
│   ├── config.toml           # Theme, page width
│   └── secrets.toml.example  # Template — copy to secrets.toml (gitignored) for local dev
├── app.py                    # Streamlit entrypoint
├── requirements.txt
├── .gitignore
├── README.md                 # Student-facing
└── CLAUDE.md
```

---

## Data contracts

### Raw DDR XML

Source: Volve dataset (Equinor public release). DDR files extracted via the `attilanagy1986/volve-app` GitHub mirror. Each well has 20-40 daily XML files. Schema is WITSML-flavored — key fields:

- `wellbore_id`, `report_date`, `report_number`
- `depth_md_start`, `depth_md_end`, `depth_tvd_end`
- `activities[]` — each with `iadc_code`, `phase`, `start_time`, `end_time`, `remarks`
- `mud_properties` — density, viscosity, pH
- `bha_data`, `bit_data` (when present)

### Parsed JSON (`/data/parsed/{well_id}.json`)

Produced by `scripts/parse_ddrs.py`. One file per well. Shape:

```json
{
  "well_id": "15/9-F-14",
  "metadata": {
    "spud_date": "2007-12-04",
    "planned_days": 19.0,
    "td_md_target_m": 4000
  },
  "daily_reports": [
    {
      "day": 1,
      "date": "2007-12-04",
      "depth_md_end_m": 121,
      "depth_tvd_end_m": 121,
      "activities": [
        { "iadc_code": "1", "phase": "drilling", "hours": 8.5, "remarks": "..." }
      ],
      "mud": { "density_sg": 1.05, "viscosity_cp": 42 },
      "remarks_combined": "Spudded 36\" hole..."
    }
  ]
}
```

**Contract rule:** every tool that consumes well data accepts this shape. If you add a tool that needs new fields, add them to `parse_ddrs.py` and re-parse — don't fetch at runtime.

### Tool I/O

All tool schemas live in `agent/tools.json` in Anthropic's tool-use format. Each tool's input is validated against its `input_schema` before dispatch. Outputs are JSON-serializable Python dicts.

---

## The agent loop (`runner.py`)

Standard tool-use loop, but the loop talks to an `LLMClient` abstraction rather than the Anthropic SDK directly. Implemented as a **generator** that yields normalized events so the Streamlit UI can render them incrementally.

```python
from agent.llm_client import get_client, ToolUse, TextDelta, EndTurn

def run_agent(user_message: str, mode: str = "triage"):
    client = get_client()  # picks AnthropicClient (or future GeminiClient) via LLM_PROVIDER
    messages = [{"role": "user", "content": user_message}]
    while True:
        assistant_blocks = []
        tool_uses = []
        for event in client.stream_with_tools(SYSTEM_PROMPT, TOOLS, messages):
            if isinstance(event, TextDelta):
                yield event
                assistant_blocks.append({"type": "text", "text": event.text})
            elif isinstance(event, ToolUse):
                yield event
                assistant_blocks.append({"type": "tool_use", "id": event.id, "name": event.name, "input": event.input})
                tool_uses.append(event)
            elif isinstance(event, EndTurn):
                if event.stop_reason == "end_turn":
                    return
                # else stop_reason == "tool_use" — fall through to dispatch
        # Dispatch
        tool_results = []
        for tu in tool_uses:
            result = dispatch_tool(tu.name, tu.input)
            tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": json.dumps(result)})
        messages.append({"role": "assistant", "content": assistant_blocks})
        messages.append({"role": "user", "content": tool_results})
```

In `app.py`, the UI consumes the generator and routes events to `st.empty()` placeholders:

```python
memo_placeholder = st.empty()
log_expander = st.expander("Agent thinking", expanded=True)
memo_buf = ""
for event in run_agent(user_message, mode):
    if isinstance(event, TextDelta):
        memo_buf += event.text
        memo_placeholder.markdown(memo_buf)
    elif isinstance(event, ToolUse):
        log_expander.write(f"→ {event.name}({event.input})")
```

Tool dispatch is a dict lookup in `tool_implementations.py`:

```python
TOOL_REGISTRY = {
    "load_well_data": load_well_data,
    "classify_activities": classify_activities,
    # ...
}
def dispatch_tool(name, input_dict):
    return TOOL_REGISTRY[name](**input_dict)
```

### Swapping LLM providers

`agent/llm_client.py` defines an abstract `LLMClient` with one method, `stream_with_tools(system, tools, messages) -> Iterator[Event]`. Today the only implementation is `AnthropicClient`. To add Gemini (or any other provider):

1. Create `agent/gemini_client.py` with a `GeminiClient(LLMClient)` subclass. Translate `tools.json` (Anthropic format) into Gemini's `function_declarations` at call time.
2. Register it in the provider dispatch in `llm_client.py:get_client`.
3. Set `LLM_PROVIDER = "gemini"` and `GEMINI_API_KEY = "..."` in your secrets.

The agent loop, tool implementations, and UI never need to change.

---

## How to add a new tool

Three files to touch, no orchestration changes needed:

1. **`agent/tools.json`** — add the schema. Use a verb-first name and write a description that explains *when* the agent should pick this tool (not just what it does). The agent picks tools by description, so be specific.

2. **`agent/tool_implementations.py`** — write the Python function. It receives the kwargs validated against `input_schema`. Return a JSON-serializable dict. Pure function over the parsed cache when possible — no network, no XML re-parsing.

3. **Register it** in `TOOL_REGISTRY` at the bottom of `tool_implementations.py`.

If the tool returns data that should appear in the memo, also touch:

4. **`templates/morning_memo.md`** — add the placeholder.
5. **`agent/system_prompt.md`** — mention the tool in the relevant workflow section so the agent knows when to call it.

**Example: adding a `compare_to_offset_wells` tool**
- Schema in `tools.json` with `well_id` and `offset_radius` inputs
- Implementation pulls subject well + nearby wells from cache, returns a comparison dict
- Mention it in the system prompt under "Triage mode" as an optional step
- Add a `{CHART:offset_comparison}` placeholder in the memo template

---

## How to add a new mode

The system prompt has three modes today: Triage, Q&A, Fleet. To add a fourth (e.g., **Alerting mode** — proactively flag concerning patterns across the fleet):

1. Add the mode definition to `agent/system_prompt.md` under "Your modes," including the workflow steps and the tool sequence
2. Add any new tools per the recipe above (e.g., `detect_fleet_anomalies`)
3. Add the new mode to the sidebar selector in `app.py`
4. Optionally add a new template in `templates/` if the output format differs from the morning memo

The agent loop itself doesn't change — modes are purely a system-prompt construct.

---

## System prompt extension

`agent/system_prompt.md` is loaded verbatim as the `system` param. Sections designed to extend:

- **Modes** — add new modes here, each with its own workflow steps
- **Taxonomies** — the NPT categorization taxonomy is a table; add categories or rename them here. Keep it in sync with the `npt_subcategory` enum in `classify_activities` tool output.
- **Style guidelines** — voice rules for the memo
- **Honesty rules** — out-of-scope topics, when to say "data inconclusive"

When extending, prefer adding a new section over editing existing ones — keeps git diffs clean and lets you A/B test prompt changes.

---

## Memo template extension

`templates/morning_memo.md` is a Markdown skeleton with `{PLACEHOLDERS}` and `{CHART:type}` markers. The `write_morning_memo` tool fills placeholders; chart markers are passed through to the UI, where `ui/memo.py` splits the Markdown on the markers and renders the corresponding plotly chart between the segments.

To add a new section:
1. Add it to the template with `{PLACEHOLDER}` markers
2. Add the corresponding inputs to the `write_morning_memo` tool schema
3. Update the tool implementation to substitute them

To add a new chart type:
1. Add it to the `build_chart` tool's `chart_type` enum in `tools.json`
2. Implement the chart-spec builder in `tool_implementations.py`
3. Add a Plotly figure factory to `ui/charts.py` keyed by the same `chart_type`

---

## Conventions

- **Cache, never re-parse.** All XML parsing happens at build time in `parse_ddrs.py`. Tools read from `/data/parsed/`. Non-negotiable for runtime reliability.
- **Tools are pure functions.** No I/O beyond the local cache. No network calls inside a tool.
- **Stream incrementally.** The agent runner is a generator; the UI rewrites Streamlit placeholders as events arrive. Don't block the UI on the full agent response.
- **No hardcoded well IDs in code.** Pass them through Streamlit widgets, env vars, or function args.
- **Errors return structured data, not exceptions.** Tools that can't find a well return `{"error": "well_not_found", "well_id": "..."}` and let the agent decide how to respond.
- **No emoji in agent outputs.** Drilling supts don't write with emoji. The voice is the product.
- **Hard cap memo body at 200 words.** Enforce in the system prompt; verify in the renderer with a word count.
- **Secrets only via `st.secrets`.** Never `os.environ` in user-facing Streamlit code, never `.env` files. The `os.environ` fallback in `llm_client.py` exists only so pytest and CLI scripts can run.
- **No vendor SDK imports outside `agent/llm_client.py`.** The runner, tools, and UI must remain provider-agnostic.

---

## Build & run

```bash
# One-time setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure secrets for local dev
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Edit .streamlit/secrets.toml and paste your ANTHROPIC_API_KEY

# Parse DDRs (run once after pulling raw XMLs into data/raw/)
python scripts/parse_ddrs.py --input data/raw/ --output data/parsed/

# Run the app
streamlit run app.py

# CLI smoke test (no UI)
python scripts/run_agent.py --well 15/9-F-14 --mode triage

# Tests
pytest tests/
```

Required secrets / env vars:
- `ANTHROPIC_API_KEY` — for the agent (set in `.streamlit/secrets.toml` for local dev, or in the Streamlit Cloud dashboard for the deployed app)
- `LLM_PROVIDER` (optional, default `"anthropic"`) — future swap point for `"gemini"` etc.

---

## Extension ideas (roadmap placeholders)

Natural next tools to add — each is a 1-2 hour change following the recipe above:

- `compare_to_offset_wells` — pull nearby Volve wells, compute deltas
- `detect_fleet_anomalies` — proactive cross-well scanning, alert mode
- `estimate_cost_impact` — apply rig day-rate + NPT to compute $ lost
- `generate_lessons_learned` — write a lessons-learned bullet list from notable events
- `compare_two_wells` — side-by-side analysis, "why was F-5 faster than F-1"

Provider swaps are also first-class: add `agent/gemini_client.py` to give students a free-tier path.

Multi-agent extensions are also possible — e.g., a drilling-supt agent orchestrating specialized geomechanics or fluids sub-agents. Each sub-agent would be a separate runner with its own tool set, called via a new `delegate_to_agent` tool.

---

## See also

- `docs/DEMO_PLAN.md` — webinar talk track, sprint plan, demo-specific risk mitigations
- `agent/system_prompt.md` — full agent behavior spec
- `agent/tools.json` — tool catalog
- `templates/morning_memo.md` — output format
- `README.md` — student-facing quickstart and "build your own" guide
