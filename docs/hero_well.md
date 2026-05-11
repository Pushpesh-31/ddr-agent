# Hero well — 15/9-F-4 (the eval rubric)

> The agent should recover the story below from the parsed DDRs alone. If the morning memo for `15/9-F-4` matches this narrative, the agent is working. If it doesn't, fix the agent — don't change the story.

## Headline numbers (computed from `data/parsed/15_9-F-4.json`)

- **Operator:** Statoil
- **Rig:** Mærsk Inspirer
- **Spud:** 2007-03-10 — **Drill complete:** 2008-02-28
- **Operational days:** 107 actual / 81.4 planned → **+25.6 days over plan**
- **TD:** 3,510 m MD / 3,138.8 m TVD
- **NPT:** 26.3 days lost (24.6%)
- **Calendar span:** 319 days (Jun 2007 to Apr 2008 — multiple campaigns)

## The story (what the memo must surface)

**F-4 ran 26 days over plan. Two events drove almost all of the overrun:**

1. **Day 255–261 (2008-02-19 to 02-25): MDT fishing job at TD.**
   While running an MDT (Modular Dynamics Tester) on wireline at 3,245 m MD, the toolstring got stuck during pressure-point sampling. Crew applied max 12,000 lbs tension, recovered fluid samples through the stuck tool, then rigged up for a cut-and-thread fishing operation. Ran 5½" DP over 7/48 wireline in singles from surface down to ~2,595 m over four days, finally POOH the fish to 3,750 m on Day 261. **~5.7 days lost.**

2. **Day 282–295 (2008-03-17 to 03-30): extended weather window.**
   Right after the fishing recovery, the rig hit a long weather window — most days were full 24-hr WOW. **~10 days lost to weather** alone, almost back-to-back.

3. **Background mechanical NPT (Days 134, 138, 149-150, 253-254, 282, 305):**
   Scattered repair/maintain events, each 8-20 hrs, totaling another ~5-7 days. None individually catastrophic; cumulatively they're the third-largest NPT bucket.

## Top three NPT drivers (expected)

1. **fishing** — ~5.7 days (138 hrs of `interruption -- fish` on Days 255-261)
2. **weather** — ~12 days total (heavy concentration in March 2008, extended WOW after the fish)
3. **mechanical** — ~5-7 days scattered (`interruption -- repair` and `interruption -- maintain`)

## What a good recommendation looks like

Concrete, rooted in the data, drilling-supt voice:

> *"On the next well in this template, plan a contingency budget for MDT fishing — F-4's stuck-tool event at 3,245 m cost 5.7 days. Pre-stage cut-and-thread fishing kit and brief crew on the procedure before running MDT on wireline below 3,000 m."*

Or:

> *"March weather windows in this field are a real risk — F-4 lost 10 consecutive days WOW after the fishing recovery. If the next well is on a Q1 schedule, build 7-10 days of weather contingency into the plan."*

## Anti-patterns (what the memo must NOT do)

- Invent the IADC code "20" for the fishing event — Volve doesn't use numeric IADC codes; the proprietary code is `interruption -- fish`. Use it verbatim or paraphrase.
- Claim a BHA twist-off — there isn't one in F-4. The fish was a wireline tool, not BHA.
- Cite "Day 12" or "Day 18" — those are from the CLAUDE.md illustrative example, not from this well.
- Quote depths that don't appear in the parsed JSON.
