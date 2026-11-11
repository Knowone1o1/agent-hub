# Agent Lab: build plan (draft for approval)

A new **Lab** section inside Agent Hub for multi-agent communication experiments, built to run the pilot in
*Organisational design for efficient AI teams* (revised proposal, 27 Oct 2026) and to grow into supply-chain work.

## Decisions so far
| Topic | Decision |
|---|---|
| Engine | Claude Code on your subscription. Exact tokens per call plus Claude Code's API-equivalent dollar estimate. Engine is pluggable, so an API-key engine can be added later for provider-billed dollars. |
| Flow | Each network picks **Rounds** (scheduler, loops allowed) or **Pipeline** (prompt flows down the arrows once). |
| Scope | Full pilot testbed: canvas, engine, protocols D0–V3, cases, scoring, batch studies, analysis, exports. |
| Memory | "Reset agents" gives every agent a fresh start; past runs, traces and stats are kept. |
| Cases | Software incidents (formal pilot) and a small supply-chain set (exploratory). I draft them, you review and freeze. |
| Model | Sonnet for experiment agents, identical across conditions within a study. |
| Scoring | Root cause and evidence IDs scored automatically; remediation scored by a blinded Claude judge; you spot-check a random sample in a blinded review screen. |

| Roles | Predetermined role packs with locked instructions. Software: Coordinator, App Logs, Database, Deploy/Config, Infra/Network. Supply chain: Coordinator, Demand, Inventory, Logistics, Supplier. Agents can be added, removed and connected, but roles can't be rewritten. |
| Agent score | Every agent gets a 0–100 score per run, shown on its deck card, from a formula fixed **before** the experiment (see below). |

## Agent score (formula v1, locked before held-out runs)
| Part | Coordinator | Specialists |
|---|---|---|
| Outcome (35) | Team joint result: cause 40% / evidence 30% / remediation 30% | Share of required evidence in its domain that it surfaced before the final answer, and whether the final answer cites its evidence |
| Accuracy (25) | Cited evidence IDs are valid and not distractors; no use of corrected evidence after the correction | same |
| Efficiency (20) | 100 at ≤50% of its fair token share (run cap ÷ agents), falling linearly to 0 at 150% | same |
| Communication (20) | 100 minus penalties: messages to unconnected agents, malformed replies, empty turns, near-verbatim relays | same |

- **Free prompts.** Runs without an answer key score only efficiency and communication, and are labeled *partial*.
- **Versions.** The formula is versioned; raw data is kept, so a later formula can be recomputed and labeled *exploratory*. v1 stays the reported one.
- **Not the headline.** Per-agent scores explain team results; they are not the study's primary endpoint, which stays team-level cost per joint success.

## What you'll see
**Lab** gets five tabs. The prompt bar is always docked at the bottom of the Canvas.

1. **Canvas**
   - Drag agents from a palette, drag from one agent's handle to another's to draw an arrow, and right-click to delete.
   - Add or remove agents freely. Click an agent to edit its role, instructions, model and evidence permissions, and to mark it as coordinator.
   - Network settings: mode (Rounds/Pipeline), protocol (Free / D0 / V1 / V2 / V3) and caps (rounds, tokens, time).
   - During a run, agents light up while working and messages animate along the arrows.
   - **Reset agents** button.
2. **Agent deck**: one card per agent, per run and all-time.
   - Input, output and cached tokens; cost estimate; number of calls.
   - Messages sent and received; evidence it saw.
   - Wait time, average response time, share of total run cost.
   - What it produced: findings, requests and alerts, each with a link into the trace.
3. **Runs**: list of runs. Each opens a trace timeline (rounds, evidence releases, every message with provenance, every model call), shows its score, and can be exported.
4. **Cases**
   - A case editor: hidden answer key, evidence items (owner role, release round, clue / distractor / correction, source), remediation rubric.
   - A case registry: difficulty, which mechanisms the case gives a chance to show, possible favoritism notes.
   - Draft → review → frozen workflow, recording who wrote and who approved each case.
5. **Studies & analysis**
   - Build the 4 conditions × 8 cases × 3 repetitions matrix, with seeds and randomized run order.
   - Budget forecast from the feasibility runs.
   - Batch runner that runs in the background, pauses when it hits a usage limit, and resumes.
   - Feasibility gate: D0 success at least 70%, at most 10% of runs ended by a cap.
   - Results: cost per joint success, cost per attempt, success broken down into cause / evidence / remediation, token composition, paired per-case outcomes, and bootstrap ranges by incident.
   - Exports: CSV, JSONL and charts.

## How a run works (engine)
- **Agent replies are structured.** Each one carries messages (to, content, evidence IDs), requests, contradiction alerts, or a final answer (root cause, evidence IDs, remediation). Malformed replies are logged and charged, never dropped.
- **Rounds mode.** At the start of each round, scheduled evidence is released to the agents permitted to see it. Every eligible agent then acts once, sending along its arrows only.
  - The run ends on a supported final answer, the deadline, the budget cap, or an explicit "unresolved".
- **Pipeline mode.** The prompt flows through the arrows in order; each agent runs when all its inputs have arrived.
- **Protocols** are rules plugged into the router:
  - **D0:** everything goes through the coordinator.
  - **V1:** relay bypass on frozen sender→recipient→topic paths. Executed before any coordinator call, and missed decisions are logged.
  - **V2:** agents post short notices and others pull the full content. Critical corrections are always delivered.
  - **V3:** a contradiction alert pauses only the affected dependency until the evidence owner verifies it.
- **Logging.** Every model call is logged with tokens (input / output / cache read / cache write), cost, timing and which messages it was shown. Waits, retries, failures and deadlocks are all charged.
- **Fairness.** Experiment agents run with Claude Code's own tools and default system prompt turned off. This keeps the overhead small and identical across conditions.

## Progress
- **Phase 1: done (11 Nov 2026).**
  - Lab page at `/lab`: canvas, role palette, inspector, agent deck with v1 scores, trace, runs list, prompt bar, Reset agents, limits.
  - Engine in `lab.py` with Rounds and Pipeline modes.
  - Tested with real runs.
  - Lesson: when all evidence sits in the shared prompt, the coordinator solves it alone. Phase 3 cases must give each specialist only its own evidence.

- **Phase 2: done (11 Nov 2026).**
  - **Protocols as router rules:** D0, V1, V2 and V3 are built on top of D0, so each variant changes one mechanism.
    - D0: routes specialist-to-specialist messages via the coordinator.
    - V1: drawn specialist arrows are bypass paths with topic filters; findings with evidence go direct and the coordinator gets a notice.
    - V2: notices with handles, pulled on request, corrections always in full.
    - V3: contradiction alerts pause only the affected agent until the evidence owner verifies, with a timeout and an alert limit.
  - **Message kinds and topics:** finding, request, reply, correction, decision.
  - **Termination states:** final answer, round/budget/time cap, unresolved, stalled, deadlock.
  - **Mechanism metrics:** relay candidates, bypasses, unused notices and detail, pull delay, alerts valid/false, paused rounds, recovery rounds.
  - **Private briefing:** prompt lines `@Agent: ...` go only to that agent.
  - Tested all four protocols on a supply-chain case (Haiku test copies); all four reached the correct root cause.
- **Scope change (11 Nov 2026):** supply chain is the first scenario.
  - Supply-chain team (D0) is the default network, with richer role instructions and suggested V1 bypass paths.
  - Phase 3 writes supply-chain cases first.
  - The proposal's software-incident framing should be updated or this recorded as a deviation.

- **Phase 3: done (11 Nov 2026).**
  - **Case format** (`cases.py`): shared brief; evidence owned by a role and released privately at a fixed round (clue / distractor / correction / context); hidden answer key (root cause, cause keywords, required and supporting evidence, points-based remediation rubric); registry (features, mechanism opportunities, timing, favoritism).
  - **Workflow:** draft → reviewed → frozen, with author, reviewer and approver. Frozen cases are locked; unfreezing needs a recorded reason. A version fingerprint goes into every run.
  - **Lab → Cases page:** editor, registry table, feature-coverage matrix. The prompt bar can run a case on any matching network.
  - **12 AI-drafted supply-chain cases** (`tools/seed_supply_cases.py`): 4 development, 8 held-out, covering every feature in the proposal's matrix. All are **draft** and need human review before freezing.
  - Tested: workflow rules, validation, and a held-out case run with correct staged release; the correction arrived in round 3 and the team got the right cause.
  - Software cases are postponed (supply chain first).

- **Phase 4: done (11 Nov 2026).** Scoring v1 (`scoring.py`).
  - **Root cause:** keyword groups from the answer key (automatic).
  - **Evidence:** every required ID must be cited (automatic; recall and distractors are reported).
  - **Action plan:** a blinded Claude judge (Sonnet) scores the rubric; ≥ 60% of points = acceptable.
  - **Joint success** = all three.
  - **Safeguards:** the judge's own cause verdict is recorded, and disagreements with the keyword check are flagged for human review. Judge cost is kept separate from team cost. Each run stores a snapshot of its case (`case.json`) so re-scoring is reproducible.
  - **Agent score:** outcome and accuracy now computed. Accuracy covers valid IDs, with −20 for using evidence after it was corrected.
  - **Lab → Review:** blinded spot-check of a random 20% sample (min 5) with no protocol, network, model, cost or judge score shown, plus judge-vs-human agreement statistics.
  - **Tested** on two live case runs. The test showed a too-lenient keyword group in `sc-ho-06` ("demand"), now fixed. Lesson: check every answer key's keywords during case review.

- **Phase 5: done (11 Nov 2026).** Studies (`studies.py`, Lab → Studies).
  - **Design:** conditions × cases × repetitions. Feasibility studies use development cases; main studies use held-out cases.
  - **Lock (preregistration):** snapshots the network, the exact case versions and the scoring settings, then builds a seeded random run order with seeds aligned per case and repetition. Main studies need frozen cases and a passing feasibility gate, or a written override that is recorded as a deviation. Repetitions can be reduced equally only before the first run, also logged as a deviation.
  - **Background runner:** one run at a time, each scored right after. On a Claude usage limit the attempt is kept (counted as an infrastructure retry) and the study pauses, then resumes automatically after a set wait. Pause, resume and stop are available; after a server restart the study comes back paused.
  - **Budget:** forecast per condition from measured runs (team + judge cost) against the ceiling (default $350, from the proposal).
  - **Feasibility gate:** D0 ≥ 70% joint success, ≤ 10% cap terminations, ≥ 10 attempts.
  - **Analysis:** per condition: joint success (cause / evidence / plan), cost per joint success, cost per attempt, tokens, messages, coordinator calls, handoffs, rounds, time, cap terminations, V1/V2/V3 mechanism measures, judge cost. Paired per-case table. Bootstrap ranges (2,000 resamples of whole cases) for success rate and cost per success, and for the difference vs D0. Cost-vs-success chart.
  - **Exports:** runs.csv (all attempts, marked evaluated or infrastructure retry), agents.csv, traces.jsonl, analysis.json. Downloads are also archived under `data/lab/studies/<id>_exports/`.
  - **Tested:** a 4-run feasibility study on Haiku. Runner, scoring, analysis, gate, forecast and exports all work. In that test both V1 runs hit the 6-round cap without a final answer, while D0 needed 5–6 rounds. Lesson: set caps from the feasibility runs (above the 90th percentile of rounds actually used), not from guesses.

## Next steps for the experiment
1. Review and freeze the 12 supply-chain cases (Lab → Cases). Check every answer key's keywords.
2. On the Sonnet supply-chain team, run a feasibility study on the 4 development cases × D0–V3 with at least 3 repetitions (≥ 10 attempts). Look at rounds and cost, then set the limits (rounds, cost, time) with headroom.
3. Do a few blinded spot-checks (Lab → Review) to see whether the judge agrees with you.
4. Create the main study (8 held-out cases × D0–V3 × 3 reps = 96 runs), check the forecast against the ceiling, lock, and run.

## Build phases (each ends with me testing it)
1. **Framework**: Lab section, canvas (drag, connect, add/remove), prompt bar, Rounds and Pipeline engine, Reset agents, agent deck, run trace.
2. **Protocols**: D0, V1, V2 and V3 as selectable router rules, plus caps and termination states.
3. **Cases**:
   - Case format, editor, registry and freeze workflow.
   - I draft software cases: 4 development and 8 held-out.
   - I draft supply-chain cases: 2 development and 4 exploratory.
4. **Scoring**: automatic cause and evidence checks, blinded remediation judge, blinded spot-check review screen.
5. **Studies**: matrix builder, batch runner, feasibility gate, analysis page, CSV/JSONL/chart exports.

Phases 1–2 give you the interactive framework you described. Phases 3–5 turn it into the pilot testbed.

## Limits to be aware of (and to disclose in the report)
- **Dollars are estimates.** Claude Code reports API-equivalent dollars, not a bill. The proposal treats provider-billed dollars as authoritative, so either record this as a deviation or add the API engine for the formal runs.
- **No model seeds.** Claude Code can't seed the model's sampling. Seeds will control scheduling and run order only.
- **Usage limits.** 96 Sonnet runs with five agents is roughly 3,000–5,000 model calls. On a subscription that will likely take several days of batches. The runner pauses and resumes on its own.
- **Case-author bias.** I'm drafting the cases, which the proposal flags as a risk. The app records authorship, and the study should note it unless a practitioner or reviewer checks the cases.
- **Testing uses your usage.** Each phase's tests run real Claude calls, kept small.
