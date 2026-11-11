# Agent Hub

A desktop app for Windows where ChatGPT and a team of Claude agents work together, plus an **Agent Lab** for
experiments on how multi-agent teams communicate (starting with supply-chain disruptions).

No API keys and no per-use billing: Claude agents run on your Claude Code login, ChatGPT runs in the ChatGPT app.

## Install and start
Requirements: Windows 10/11, Python 3.10+, [Claude Code](https://claude.com/claude-code) signed in, and
`pip install -r requirements.txt` (installs pythonnet for the native window). The .NET Desktop runtime and the
WebView2 runtime ship with current Windows 11.

Start the app: `pythonw desktop\app.py` (or double-click `start.bat`). It opens in its own window, not a browser.
- `--lab` opens straight into the Agent Lab, `--dashboard` opens the team dashboard (for a second monitor)
- The engine (`server.py`) starts invisibly and stops when you close the app, unless a Lab study is still running
- Logs: `data/server.log`

## Chats and projects
- **Chats**: one-on-one with Claude (automatic) or ChatGPT (copy-paste relay).
- **Projects**: a team led by a manager. By default ChatGPT manages and Claude agents (Researcher, Builder, Reviewer) do the work.
  ChatGPT assigns tasks with `@Name`; those agents run automatically and their results go back to ChatGPT.
- **Agents & teams**: build agents (job, instructions, engine, model) and save teams.
- **About me**: a shared profile every agent reads (`profile.md`, never uploaded; see `profile.example.md`).

## Agent Lab
A lab for controlled experiments with multi-agent communication (`/lab`, or the 🧪 button in the app).

| Tab | What it does |
|---|---|
| **Network** | Drag agents onto a canvas and draw arrows for who may talk to whom. Fixed role packs (Supply chain, Software, General). Prompt bar to start a run; live view; agent deck with per-agent scores, tokens and cost; full trace. |
| **Cases** | Test cases: a shared brief, evidence each specialist receives privately at set rounds (clues, distractors, corrections), and a hidden answer key and rubric. Draft → reviewed → frozen workflow. |
| **Studies** | Every protocol × every case × several repetitions, preregistered and run in the background, with results, uncertainty ranges and CSV/JSONL exports. |
| **Review** | Blinded spot-check: you score a random sample of answers yourself to check the AI judge. |

### The communication protocols, in plain words
Every protocol uses the same team: one **Coordinator** (the boss who decides) and four **specialists**, for
supply chain Demand, Inventory, Logistics and Supplier. Each specialist only sees its own evidence, so they must
communicate to solve the case. The protocols differ only in **how information travels**:

- **Free**: no rules beyond your arrows. Anyone may message anyone they have an arrow to. Good for exploring.
- **D0 · Coordinator-led (the baseline)**: everything goes through the boss. Specialists report to the
  Coordinator, and if one wants to tell another specialist something, the message goes to the Coordinator, who
  decides whether to pass it on. *Like a classic hierarchy: well-coordinated, but the boss becomes a bottleneck and spends
  effort just forwarding.*
- **V1 · Relay bypass**: like D0, but a specialist may send a **finding backed by evidence** straight to another specialist
  along a pre-approved path (e.g. Logistics → Inventory about delays). The Coordinator just gets a short "FYI". Questions and
  decisions still go through the boss. *Saves the boss's time; the risk is that the boss misses something important.*
- **V2 · Pull information**: like D0, but instead of sending the full report, a specialist sends a **one-line notice**
  ("I have the transport report, handle H3"). Whoever needs the details asks for them. Corrections are always sent in full.
  *Saves reading time on details nobody needs; the risk is that someone doesn't realise they needed it.*
- **V3 · Early escalation**: like D0, plus an **alarm button**. If an agent sees evidence that contradicts something the
  team relies on, it raises an alert: only the affected agent pauses, the owner of the evidence checks it, and work resumes.
  *Stops the team building on a wrong fact; the risk is false alarms that slow things down.*

**Methods from recent research** (details, sources and simplifications in [docs/METHODS.md](docs/METHODS.md)):

- **A1 · Inhibitory evidence routing** (AI research, Siboni et al. 2026): the router forwards the *original evidence* an
  agent cites, never its paraphrase or opinion, once per receiver per round, and cancels copies receivers already have.
  *"Send me the source document, not your summary."*
- **A2 · PACT action-state messages** (AI research, Huang et al. 2026): every message is a tiny record of Action, State and
  Result; reasoning and small talk are stripped. *A strict status-report template.*
- **B1 · Adaptive hierarchy** (business research, Abi-Esber et al., Academy of Management Journal 2026): flat rounds while
  evidence comes in (anyone talks to anyone, no decisions), then centralized rounds where the boss collects positions and
  decides. *Brainstorm openly, then the manager pulls it together.*
- **A3 · DarkForest controlled disclosure** (AI research, Li et al. 2026): specialists don't talk; each gives a blind
  answer, and the boss sees only a table of the candidate answers, their support and confidence. A strong majority can
  override the boss. *"Less talk, higher accuracy."*
- **A4 · Blackboard with volunteers** (AI research, Salemi et al. 2025/26): the boss posts requests on a board without naming
  anyone; whoever can help volunteers, on a board only the boss reads. *Post the question to the whole team.*
- **B2 · Stand-up check-ins** (business research, Ghosh & Wu, Organization Science 2023): every other round from round 3,
  everyone broadcasts what they did, what's next, and whether the end goal changed. *The classic stand-up meeting.*
- **B3 · Quiet time with interaction windows** (business research, Perlow, Administrative Science Quarterly 1999; Bernstein,
  Shore & Lazer, PNAS 2018): two quiet rounds where messages are held and batched (duplicates removed, max 3 per agent; only
  urgent corrections get through), then one interaction round where everything is delivered and the boss acts. *Office hours.*
- **B4 · Burst threads with a novelty filter** (business research, Riedl & Woolley, Academy of Management Discoveries 2017): a
  question opens a short thread everyone answers at once and the boss must close within two rounds; late replies are dropped,
  and findings that repeat what the recipient already has are suppressed. *Short focused bursts, no repeating yourself.*

The question the Lab answers: which method makes the team **cheaper per correct answer** than D0, and what does it cost in
reliability? Each method changes exactly one thing compared with D0, so differences can be traced to that change.
Ready-made supply-chain networks for D0 and all eight methods (A1–A4, B1–B4) are set up, and **Studies → Method
comparison** compares them.

### Scoring
A run is a **joint success** when (1) the root cause matches the answer key, (2) the required evidence is cited, and
(3) a blinded AI judge rates the action plan acceptable (≥ 60% of rubric points). The main measure is
**cost per joint success**. Each agent also gets a 0–100 score (outcome, accuracy, efficiency, communication).
Costs are Claude Code's API-equivalent estimates, not a bill.

Full design notes and progress: [docs/LAB_PLAN.md](docs/LAB_PLAN.md). Cases: `data/lab/cases/` (generated by
`tools/seed_supply_cases.py`).

## Adding agents by hand (advanced)
Everything is stored in `agents.json`. Types:
- `claude_cli`: a Claude agent (`model`: `"sonnet"`, `"opus"`, `"haiku"`, ...)
- `manual`: any chat app you relay by hand (Gemini, Copilot, a second ChatGPT persona...)
- `codex_cli`: OpenAI's Codex CLI with your ChatGPT plan (`npm install -g @openai/codex`, `codex login`)
- `openai_compatible`: any OpenAI-style endpoint, e.g. a free local model in Ollama
- A new kind of bot: write `run_x(agent, prompt) -> str` in `server.py` and add it to `ADAPTERS`.

## Where things are
| Path | Contents |
|---|---|
| `server.py` | the local engine (chats, projects, API) |
| `lab.py`, `cases.py`, `scoring.py`, `studies.py` | Agent Lab engine, cases, scoring, studies |
| `static/` | the app's screens |
| `desktop/` | the native window (WebView2) |
| `data/` | your conversations, runs and results: **local only, not in git** (except `data/lab/cases/`) |
| `shared/` | files the Builder agent writes (local only) |
