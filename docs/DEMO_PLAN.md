# DDR Triage Agent — Webinar Demo Plan

> Demo-specific plan for the SPE young professionals webinar. Mixed upstream technical audience. 9-minute live segment.
> This doc is temporary — once the webinar ships, archive or delete. Engineering details live in `CLAUDE.md`.

---

## Demo arc (9 minutes)

| Time | Beat | Notes |
|------|------|-------|
| 0:00–0:45 | Hook | Talk over a screenshot of a real Volve DDR XML. *"It's 6 AM. Drilling supt has 5 new DDRs and 30 min before the meeting."* |
| 0:45–2:00 | Setup | Open UI, single well loaded (recommend 15/9-F-14 — known troublemaker). Show the file tree. |
| 2:00–5:00 | **Live agent run** | Click "Triage this well." Stream tool calls visibly. Pause to read the memo aloud. |
| 5:00–6:30 | Free-text Q&A | Take an unscripted question. Plant if needed: *"What was the worst day on this well?"* |
| 6:30–8:00 | **One more thing** | Click. Fleet view across all 24 wells. NPT pareto. Ask: *"Rank wells by fishing NPT %."* |
| 8:00–9:00 | Close | Three things made this agentic: planned, chose tools, made judgment calls. Point at Claude Code + MCP. |

---

## Sprint plan

### Saturday morning (4 hr) — data and shell
| Hr | Task |
|----|------|
| 1 | Clone `attilanagy1986/volve-app`. Pull the `Reports` folder. Confirm XML schema by opening 2-3 files. |
| 2 | Write `scripts/parse_ddrs.py`. XML → JSON per well. Validate on all 24 wells. Cache to `/data/parsed/`. |
| 3 | Eyeball the parsed data. Pick 1 well for the deep-dive (look for: rich activity diversity, real NPT events, clear story arc). Write a one-paragraph "well story" for that well — this is what the agent should be able to recover. |
| 4 | `npx create-next-app`. Tailwind. Empty shell with three routes: `/`, `/well/[id]`, `/fleet`. |

### Saturday afternoon (4 hr) — UI
| Hr | Task |
|----|------|
| 5 | Well selector + DDR file tree (left panel). |
| 6 | Days-vs-depth chart component (Recharts). Drive from parsed JSON, no agent yet. |
| 7 | NPT pareto component. Activity log component (will stream agent reasoning). |
| 8 | Wire `/api/agent` route. Stub it — return canned response for now. |

### Sunday morning (4 hr) — the agent
| Hr | Task |
|----|------|
| 9 | Implement the 7 tools (see `agent/tools.json`). Most are pure functions over the parsed JSON. |
| 10 | Tool-use loop in `runner.py`. End-to-end on chosen well. Iterate until memo quality is acceptable. |
| 11 | Memo renderer in UI (Markdown → React). Q&A chat interface. |
| 12 | Stream tool calls to the UI. Make the agent thinking *visible*. |

### Sunday afternoon (3 hr) — polish + safety net
| Hr | Task |
|----|------|
| 13 | Fleet view route. Aggregate KPIs across all 24 wells. Fleet pareto. |
| 14 | **Demo mode toggle:** keyboard shortcut that swaps live agent for cached run. Hidden, only you know it. Critical insurance. |
| 15 | Full rehearsal pass. Record a screencap of the cleanest run as backup. |

---

## Risk register (demo-day)

| Risk | Mitigation |
|------|------------|
| Anthropic API latency mid-demo | Pre-warm cached run, demo-mode toggle (`NEXT_PUBLIC_DEMO_MODE=cached`) |
| XML parsing edge cases break agent | Parse once at build time, validate, ignore broken DDRs |
| Audience asks question agent can't answer | System prompt forces "data inconclusive" rather than hallucination |
| Wifi fails | Backup screen recording, local Vercel preview running |
| Browser notifications, Slack pop-ups | Clean Chrome profile, DND mode |
| Memo too verbose, kills pacing | Tight memo template (200-word cap) |
| Agent picks wrong NPT category | Constrained to enum in tool schema; taxonomy pinned in system prompt |

---

## Hero well selection

Skim the parsed data Saturday morning. Pick one with:

- Rich activity diversity (drilling + tripping + casing + at least one significant NPT)
- A real story (fishing job, stuck pipe, sidetrack)
- Avoid: too smooth (boring) or too disastrous (distracting from the workflow point)

Suggested starting candidate: **15/9-F-14** — known to have NPT events. Confirm or swap after looking at parsed data.

---

## Plant question for live Q&A

Have one ready in case the audience freezes. Strongest options:

- *"What was the most expensive day on this well?"* — surfaces cost reasoning
- *"Compare this well's NPT % to the fleet average."* — sets up the fleet reveal
- *"Why did Day 12 take so long?"* — drills into a specific event, shows the agent reads remarks

---

## Fleet reveal — scope decision

All 24 vs. top 6 by variance? Recommend **top 6 + footnote** "(20 more wells in dataset)". Tradeoff:
- All 24: more impressive scale, harder to read on a webinar screen
- Top 6: more readable, still demonstrates fleet-level reasoning

---

## Closing slide content

Three things separate this from a chatbot:

1. **It plans.** The agent decides which tool to call next based on intermediate results, not a hard-coded sequence.
2. **It chooses.** Multiple tools available; the agent picks based on user intent (triage vs. Q&A vs. fleet).
3. **It judges.** Categorizing NPT root cause from free-text remarks is genuine judgment — same call a junior drilling engineer makes.

What it is **not**:
- Not a curve-fit wrapped in chat
- Not a single-prompt summarizer
- Not autonomous (you still review the memo)

The toolchain that builds this in a weekend: Claude Code + Anthropic SDK + Recharts + a public dataset. Total LOC: probably <800. That's the YP takeaway.

---

## Pre-demo checklist (run 30 min before going live)

- [ ] `NEXT_PUBLIC_DEMO_MODE` unset (live agent active)
- [ ] Hero well loads cleanly, memo renders end-to-end in <90 sec
- [ ] Cached run primed for fallback (keyboard shortcut tested)
- [ ] Screen recording of cleanest rehearsal saved locally
- [ ] Browser notifications off, Slack closed, DND on
- [ ] Backup screen-capture window ready (in case the UI hangs)
- [ ] Tabs minimized except the demo + one terminal for "look how simple"
- [ ] Mobile hotspot available if hotel/venue wifi fails
