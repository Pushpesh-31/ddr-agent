# DDR Triage Agent

An agentic workflow over daily drilling reports (DDRs) from the Volve field. The agent loads parsed DDRs, classifies activities, surfaces non-productive time (NPT) by category, and writes a 200-word morning memo for the drilling superintendent. It also answers free-text questions about a single well or the whole 26-well fleet.

Built for the SPE young-professionals webinar as a teaching artifact — short, legible, and deliberately easy to take apart.

- **UI:** Streamlit (single-page Python app).
- **Agent:** Anthropic SDK tool-use loop, behind an `LLMClient` abstraction so you can swap models later.
- **Data:** Equinor's public Volve dataset, ~1,380 DDR XMLs across 26 wells.

---

## Quickstart (5 minutes from clone to memo)

```bash
git clone <this-repo-url> ddr-agent
cd ddr-agent

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1) Get an Anthropic API key from https://console.anthropic.com/
# 2) Add it to a local secrets file (the file is gitignored — never commit your key)
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
$EDITOR .streamlit/secrets.toml   # paste your key into ANTHROPIC_API_KEY

# 3) Pull the Volve DDR XMLs and parse them once.
#    The mirror repo is small (~20 MB).
git clone --depth 1 https://github.com/attilanagy1986/volve-app.git /tmp/volve-app
cp /tmp/volve-app/Reports/*.xml data/raw/
python scripts/parse_ddrs.py --input data/raw/ --output data/parsed/

# 4) Run the app.
streamlit run app.py
```

In the sidebar pick a well (the hero well `15/9-F-4` is preselected), choose **Triage**, and click *Run triage*. You should see tool calls stream into the "Agent thinking" expander while a memo materializes on the left.

CLI smoke (no UI) for a faster feedback loop while iterating on tools or the system prompt:

```bash
python scripts/run_agent.py --well 15/9-F-4 --mode triage
python scripts/run_agent.py --well 15/9-F-4 --mode qa --question "What was the worst day?"
python scripts/run_agent.py --mode fleet --question "Rank wells by NPT %."
```

Tests run without the API:

```bash
pytest tests/
```

---

## How the agent loop works

The interesting code is in two files:

- `agent/runner.py` — the tool-use loop (~70 lines).
- `agent/tool_implementations.py` — seven pure functions over the parsed JSON cache.

The loop is the standard Anthropic tool-use pattern, with one twist: it talks to an `LLMClient` abstraction (in `agent/llm_client.py`) instead of importing the SDK directly. That seam is what lets us swap providers later (see "Swapping LLM providers" below).

```python
# agent/runner.py — pseudocode
client = get_client()                           # AnthropicClient today
messages = [{"role": "user", "content": prompt}]
while True:
    for event in client.stream_with_tools(system, tools, messages):
        if isinstance(event, ToolUse):           # → "→ load_well_data(...)"
            ...
        elif isinstance(event, EndTurn):
            assistant_blocks = event.raw_content
    messages.append({"role": "assistant", "content": assistant_blocks})
    if stop_reason == "end_turn":
        return
    # else: dispatch every tool_use, append tool_results to messages, loop.
```

Why this is "agentic" and not just a chatbot:

1. **It plans.** The agent decides which tool to call next based on what previous tools returned. You'll see this in the "Agent thinking" expander — it always calls `load_well_data` first, but the order and selection of charts and questions in later turns depends on what it found.
2. **It chooses.** The same loop handles three modes (Triage / Q&A / Fleet). The agent picks tools based on the system prompt's mode-routing, not a hard-coded sequence in our Python code.
3. **It judges.** `classify_activities` does the easy work (mapping `interruption -- fish` → `npt: fishing`). The interesting judgments — root-cause-from-remarks, anomaly severity, the recommendation paragraph — happen in the model itself, not in our tool functions.

For full architectural detail (data contracts, how to add a new tool, conventions), read `CLAUDE.md`.

---

## Use your own dataset

The agent doesn't know anything about Volve specifically. It knows the **parsed JSON contract** in `CLAUDE.md` and reads `data/parsed/<well_id>.json`. To swap in your own DDRs:

1. Write a parser that produces files matching the contract:

```json
{
  "well_id": "<your well>",
  "metadata": {
    "spud_date": "...", "operator": "...", "rig_name": "...",
    "n_reports": 30, "total_days": 30, "planned_days": 25.0,
    "td_md_m": 4012, "td_tvd_m": 3847
  },
  "daily_reports": [
    { "day": 1, "date": "...", "depth_md_end_m": 121,
      "activities": [
        { "code": "drilling -- drill", "phase": "drilling", "hours": 8.5,
          "depth_md_m": 121, "remarks": "..." }
      ],
      "mud": { "density_sg": 1.05, "viscosity_cp": 42, "type": "..." },
      "remarks_combined": "..." }
  ]
}
```

2. Drop the files into `data/parsed/`. (XMLs do not need to live in `data/raw/` — that's a Volve convention. The agent reads only from `data/parsed/`.)

3. Update `_CATEGORY_RULES` and `_REMARK_HINTS` in `agent/tool_implementations.py` if your activity codes differ from Volve's `<phase> -- <task>` convention.

4. Optional: if your taxonomy of NPT root-causes differs, update the table in `agent/system_prompt.md`. The agent's classification follows the system prompt verbatim, so the prompt is the source of truth for the taxonomy.

The "no XML at runtime" rule (`CLAUDE.md`'s **Conventions**) is non-negotiable — parse to JSON once at build time, commit the JSON, never re-parse during a request.

---

## Swapping LLM providers

The repo ships with `AnthropicClient` (model: `claude-sonnet-4-6`). To use a different model — for example Gemini's free tier so students don't need Anthropic credits — implement a new client:

```python
# agent/gemini_client.py (you write this)
class GeminiClient(LLMClient):
    def stream_with_tools(self, system, tools, messages):
        # Translate `tools` (Anthropic format) into Gemini function_declarations.
        # Stream a turn from google.genai.
        # Yield TextDelta / ToolUse / EndTurn — same Event types as Anthropic.
```

Then register it in `get_client()` in `agent/llm_client.py` and set in your secrets:

```toml
LLM_PROVIDER = "gemini"
GEMINI_API_KEY = "..."
```

The runner, the seven tools, and the Streamlit UI don't need to change. None of them import the Anthropic SDK directly — that's the whole point of the abstraction.

(Gemini implementation is not yet shipped — contributions welcome.)

---

## Deploy your own (Streamlit Community Cloud, free)

1. Push your fork to GitHub.
2. Go to [share.streamlit.io](https://share.streamlit.io) and connect your repo.
3. In the app's **Settings → Secrets** dashboard, add:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```
   Streamlit Cloud injects these into `st.secrets` at runtime. **Do not commit `secrets.toml`.**
4. Deploy. The first cold start takes ~30 sec; subsequent visits are instant. Idle apps sleep — pre-warm by visiting the URL ~5 min before a demo.

For a live demo, the **Demo mode (cached)** sidebar checkbox is your insurance — it bypasses the API entirely and replays a pre-recorded run from `data/cached_runs/`. To record a new clean run:

```bash
python scripts/build_cached_run.py --well 15/9-F-4
```

---

## Project layout

```
ddr-agent/
├── data/raw/             # Volve DDR XMLs (gitignored — pull from the mirror)
├── data/parsed/          # JSON cache (committed)
├── data/cached_runs/     # Demo-mode replays (committed)
├── scripts/parse_ddrs.py # One-shot XML → JSON converter
├── scripts/run_agent.py  # CLI smoke test (no UI)
├── agent/                # The interesting bit: runner + tools + LLMClient
├── ui/                   # Plotly figures + memo renderer + activity log
├── templates/            # Markdown skeleton the agent fills
├── docs/hero_well.md     # The story the agent must recover (eval rubric)
├── docs/DEMO_PLAN.md     # Webinar talk track and rehearsal checklist
├── tests/                # Pytest suite over the tool functions
├── app.py                # Streamlit entrypoint
└── CLAUDE.md             # Architecture spec — read this if you want to extend
```

---

## License & data attribution

Volve dataset is published by Equinor under their [Volve open data license](https://www.equinor.com/energy/volve-data-sharing). This repo's code is MIT (or your preferred license — set `LICENSE` to taste).
