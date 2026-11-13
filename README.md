# Agent Studio

A living, local multi-agent studio. Brief a project, let the **Principal** staff a team from your roster (and recruit
new specialists if skills are missing), then watch every agent work in real time: sleeping on the Sideline, waking up
with a glowing badge and a thought bubble with live step and progress when they're on a task.

**Runs only on your Claude subscription.** Every agent turn is a child process of your logged-in `claude` CLI
(Claude Code): `claude -p --output-format stream-json --verbose --include-partial-messages`, inside the project folder.
No Anthropic SDK, no `ANTHROPIC_API_KEY` (it is even removed from the child environment), no per-use billing.
The header badge shows **Connected via Claude Subscription (CLI)** once `claude --version` succeeds.

## Install and launch (Windows)
Requirements: Python 3.10+, and [Claude Code](https://claude.com/claude-code) installed and signed in (`claude` once).

```bash
pip install -r requirements.txt
```

```bash
start.bat
```

`start.bat` opens Agent Studio in its own window (WebView2). Prefer a browser? Run `python run.py` and it opens
http://127.0.0.1:8777. The server stops when you close the window, unless agents are still working.

## How it works
1. **＋ New project** → write a brief (goals, constraints). The Principal reads it plus the whole roster, then proposes a
   **Team Staffing Plan**: existing agents to draft (with reasons) and new specialists to recruit (name, avatar, colour,
   personality, model, why). Tick or untick, then **Approve & Assemble Team**: recruits get a `.agents/<id>.md`
   profile, the team is assigned, and `.project-cache/` is created from your brief.
2. **▶ Run sprint**: the Principal assigns Up Next tasks to idle teammates (one each); they work in parallel. **🤖 Autopilot**
   keeps planning and running sprints until the board is done (max cycles in ⚙ settings). **■ Stop** kills the turns.
3. The Principal may move agents to **the Sideline** when their phase is done, and recall them later (e.g. the Judge
   once code lands). It can never delete an agent: only you can, from the agent's drawer (with confirmation; the
   profile goes to `.agents/.trash`).
4. Click any agent for the **inspector**: bio, project, model, live terminal output, a direct prompt box, Sideline/Draft,
   Stop and Delete.

### Rules
- **One project per agent.** Agents on another project show a 🔒 and can't be drafted until released.
- **Instant onboarding.** Every turn injects the project's `CONTEXT.md`, `STATUS.md`, team list and recent changelog
  into the agent's system prompt.
- **Progress protocol.** Agents print `[STEP: …]` and `[PROGRESS: n/total | label]` while working, `[NEW_TASK: … @id]`
  for follow-ups and finish with `[RESULT: …]`. The studio parses the stream live (SSE) into bubbles, the board and
  the changelog.
- **Tools.** Agents can Read/Write/Edit/Glob/Grep inside their project folder. Terminal commands (Bash, e.g. to run
  tests) are off by default; turn them on in ⚙ settings.

## Folder structure
```
Agent Hub/
├─ run.py                     starts the server (uvicorn, port 8777; STUDIO_PORT to change)
├─ start.bat                  opens the desktop window (falls back to the browser)
├─ requirements.txt
├─ studio/                    backend (FastAPI)
│  ├─ server.py               HTTP API + Server-Sent Events (/api/events)
│  ├─ claude_cli.py           `claude --version` check, streamed `claude -p` runs, tag parsing
│  ├─ runner.py               live agent state, agent turns, sprints & autopilot
│  ├─ principal.py            brief → staffing plan, assemble team, sprint planning (bench/recall, never delete)
│  ├─ projects.py             projects, one-project-per-agent locks, STATUS.md / CHANGELOG.log / CONTEXT.md
│  ├─ agents.py               .agents/<id>.md character profiles (create, update, user-only delete)
│  └─ bus.py                  thread-safe event bus for SSE
├─ public/                    front end (vanilla JS + CSS, no build step, works offline)
│  ├─ index.html · styles.css · app.js
│  └─ avatar.js               SVG character faces (asleep / awake)
├─ .agents/                   character profiles: principal, design, algorithm, code, judge (+ recruits)
├─ templates/project/         default project template (.project-cache/CONTEXT.md, STATUS.md, CHANGELOG.log)
├─ projects/<slug>/           each project's workspace (agents' cwd) + .project-cache/ (local only, not in git)
├─ state/                     settings, logs, window data (local only)
└─ desktop/                   native window (WebView2 via pythonnet)
```

## Agent profile format (`.agents/<id>.md`)
```markdown
---
id: judge
name: Themis
role: Judge / Quality Critic
avatar: ⚖️
color: #60a5fa
model: sonnet            # sonnet | opus | haiku
tone: fair, exacting and constructive
skills: code review, testing, QA
kind: member             # "principal" for the lead
---
Free-form bio, personality and working style. This becomes the agent's persona in every turn.
```

## Costs
Turns run on your Claude plan's usage. The dollar figures shown are Claude Code's API-equivalent estimates, for
comparison only; you are not billed per use. Sleeping agents use nothing.

The previous app (Agent Hub + Agent Lab) is archived: git tag `agent-hub-v1` and `Documents\Archive`.
