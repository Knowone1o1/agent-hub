"""Live agent state, agent turns (one `claude` child process each), sprints and autopilot."""
import json
import threading
import time
from collections import deque

from . import STATE_DIR, agents, bus, projects
from .claude_cli import TAG_NEW_TASK, TAG_RESULT, Run

TOOLS_BASE = ["Read", "Write", "Edit", "Glob", "Grep"]
DEFAULT_SETTINGS = {"max_parallel": 4, "autopilot_cycles": 4, "allow_bash": False, "allow_web": False}
_lock = threading.RLock()
STATE, LOGS, RUNS, SPRINTS = {}, {}, {}, {}
TOTALS = {"cost_usd": 0.0, "runs": 0}


def settings():
    try:
        s = json.loads((STATE_DIR / "settings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        s = {}
    return {**DEFAULT_SETTINGS, **{k: v for k, v in s.items() if k in DEFAULT_SETTINGS}}


def save_settings(new):
    s = {**settings(), **{k: v for k, v in new.items() if k in DEFAULT_SETTINGS}}
    s["max_parallel"] = max(1, min(int(s["max_parallel"]), 8))
    s["autopilot_cycles"] = max(1, min(int(s["autopilot_cycles"]), 20))
    s["allow_bash"] = bool(s["allow_bash"])
    s["allow_web"] = bool(s["allow_web"])
    (STATE_DIR / "settings.json").write_text(json.dumps(s, indent=2), encoding="utf-8")
    return s


def tools(agent=None):
    """The tools an agent may use. Web access is per agent (its `web:` domain list) and only when "Allow web" is on;
    each domain becomes a WebFetch(domain:...) rule that Claude Code enforces. WebSearch is never granted."""
    s = settings()
    out = TOOLS_BASE + (["Bash"] if s["allow_bash"] else [])
    if agent and s["allow_web"]:
        out += [f"WebFetch(domain:{d})" for d in agent.get("web", [])]
    return out


def web_note(agent):
    """One line for the agent's prompt so it knows what it can (and can't) reach."""
    if not agent.get("web"):
        return "You have no web access."
    if not settings()["allow_web"]:
        return "Web access is switched off in the studio settings right now; work offline."
    return ("You may fetch web pages with WebFetch, but ONLY from these domains: " + ", ".join(agent["web"]) +
            ". Any other site is blocked. WebSearch is not available. For papers: arXiv pages work directly; many publisher "
            "sites (ACM, AAAI, OpenReview) refuse automated fetches, so get titles, abstracts and open-access PDF links from "
            "api.semanticscholar.org, api.openalex.org or api.crossref.org by DOI when those are on your list.")


# ---------------------------------------------------------------- live state

def _blank():
    return {"state": "idle", "step": None, "progress": None, "task": None, "project": None, "since": None,
            "cost_usd": 0.0, "calls": 0, "last_result": None, "error": None}


def snap(aid):
    with _lock:
        return dict(STATE.setdefault(aid, _blank()))


def _set(aid, **kw):
    with _lock:
        st = STATE.setdefault(aid, _blank())
        st.update(kw)
        out = dict(st)
    bus.publish({"type": "agent", "id": aid, "live": out})


def _log(aid, kind, text, slug=None):
    entry = {"t": time.strftime("%H:%M:%S"), "kind": kind, "text": text[:4000]}
    with _lock:
        LOGS.setdefault(aid, deque(maxlen=600)).append(entry)
    bus.publish({"type": "log", "id": aid, "entry": entry})
    if slug:
        try:
            d = projects.cache(slug) / "logs"
            d.mkdir(exist_ok=True)
            with open(d / f"{aid}.log", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{kind}] {text}\n")
        except (OSError, KeyError):
            pass


def logs(aid):
    with _lock:
        return list(LOGS.get(aid, []))


def is_busy(aid):
    return snap(aid)["state"] in ("working", "thinking")


# ---------------------------------------------------------------- prompts

PROTOCOL = """## Studio protocol (follow exactly)
- While you work, print progress tags on their own lines, early and often:
  [STEP: <what you are doing right now, a few words>]
  [PROGRESS: <current>/<total> | <label>]    e.g. [PROGRESS: 2/5 | Writing the parser]
  Choose a total number of steps at the start, then count up as you finish each one.
- Work only inside this project folder. Never edit .project-cache/STATUS.md (the studio owns the board).
- Record lasting decisions (architecture, stack, conventions, interfaces) by appending short bullets to
  .project-cache/CONTEXT.md under "## Decisions".
- If you discover follow-up work, add one line per task: [NEW_TASK: <title> @<agent-id>]  (agent id optional).
- End with exactly one line: [RESULT: <one sentence: what you delivered and where>]"""


def persona(a):
    skills = f"\nSkills: {', '.join(a['skills'])}." if a.get("skills") else ""
    return (f"# You are {a['name']} {a['avatar']}, {a['role']} in the user's AI engineering studio.\n"
            f"Write in this tone: {a['tone']}.{skills}\nWeb: {web_note(a)}\n\n{a.get('bio', '')}")


def team_lines(slug):
    meta = projects.get(slug)
    out = []
    for aid in meta.get("team", []):
        try:
            a = agents.get(aid)
            web = f" · web: {', '.join(a['web'])}" if a.get("web") and settings()["allow_web"] else ""
            out.append(f"- @{aid}: {a['name']} {a['avatar']}, {a['role']}{web}")
        except KeyError:
            pass
    return "\n".join(out) or "- (no one yet)"


def onboarding(slug):
    """Injected into every agent that works on a project: they are instantly up to date."""
    return (f"## Project onboarding: {projects.get(slug)['name']}\n"
            f"### Team\n{team_lines(slug)}\n\n"
            f"### .project-cache/CONTEXT.md\n{projects.context_text(slug)[-12000:]}\n\n"
            f"### .project-cache/STATUS.md\n{projects.status_text(slug)[-6000:]}\n\n"
            f"### Recent CHANGELOG\n" + "\n".join(f"{c['t']} {c['who']}: {c['text']}" for c in projects.changelog(slug, 15)))


# ---------------------------------------------------------------- one agent turn

def run_agent(aid, prompt, system, cwd, *, slug=None, task=None, label=None, use_tools=True, model=None, tool_list=None):
    """Blocking: one streamed `claude -p` call for this agent. Updates the live state as it goes."""
    a = agents.get(aid)
    _set(aid, state="working" if use_tools else "thinking", step=label or "Waking up…", progress=None, task=task,
         project=slug, since=time.time(), error=None)
    _log(aid, "start", f"▶ {label or 'New turn'}", slug)

    def on_step(text, tool=False):
        _set(aid, step=("🔧 " if tool else "") + text)

    def on_progress(cur, tot, lab):
        _set(aid, progress={"cur": cur, "total": tot, "label": lab})

    def on_log(kind, text):
        _log(aid, kind, text, slug)

    run = Run(prompt, system, cwd, model=model or a["model"], tools=(tool_list if tool_list is not None else tools(a)) if use_tools else [],
              on_step=on_step, on_progress=on_progress, on_log=on_log)
    with _lock:
        RUNS[aid] = run
    run.run()
    with _lock:
        RUNS.pop(aid, None)
        st = STATE.setdefault(aid, _blank())
        st["cost_usd"] += run.cost_usd
        st["calls"] += 1
        TOTALS["cost_usd"] += run.cost_usd
        TOTALS["runs"] += 1
    if slug:
        try:
            meta = projects.get(slug)
            meta["cost_usd"] = round(meta.get("cost_usd", 0) + run.cost_usd, 4)
            meta["runs"] = meta.get("runs", 0) + 1
            projects.save(meta)
        except KeyError:
            pass
    if run.error:
        _log(aid, "error", f"✗ {run.error}", slug)
    _log(aid, "end", f"■ done · ${run.cost_usd:.3f} (API-equivalent estimate, billed to your subscription)", slug)
    _set(aid, state="idle", step=None, progress=None, task=None, since=None,
         error=run.error if run.error and run.error != "stopped" else None)
    return run


def stop_agent(aid):
    with _lock:
        r = RUNS.get(aid)
    if r:
        r.stop()


# ---------------------------------------------------------------- tasks

def work_task(slug, aid, tid):
    t = next((t for t in projects.read_tasks(slug) if t["id"] == tid), None)
    if not t:
        return
    a = agents.get(aid)
    projects.update_task(slug, tid, column="In Progress", assignee=aid, note="")
    bus.publish({"type": "board", "slug": slug})
    system = f"{persona(a)}\n\n{onboarding(slug)}\n\n{PROTOCOL}"
    prompt = (f"Your task [{tid}]: {t['title']}\n\nDo the work now, in this project folder. Keep it focused on this task. "
              "Emit [STEP]/[PROGRESS] tags as you go and finish with [RESULT: ...].")
    run = run_agent(aid, prompt, system, projects.pdir(slug), slug=slug, task=f"[{tid}] {t['title']}", label=t["title"])
    text = run.text if run.final.strip() and run.final.strip()[:200] in run.text else run.text + "\n" + run.final
    results = TAG_RESULT.findall(text)
    if run.error and not results:
        projects.update_task(slug, tid, column="Up Next", note=f"⚠ {('stopped' if run.error == 'stopped' else 'failed')}: {run.error[:120]}")
        c = projects.log_change(slug, aid, f"[{tid}] {t['title']}: not finished ({run.error[:160]})")
    else:
        summary = (results[-1] if results else (run.final.strip().splitlines() or ["Done."])[-1]).strip()[:300]
        projects.update_task(slug, tid, column="Done", note=summary)
        _set(aid, last_result=summary)
        c = projects.log_change(slug, aid, f"[{tid}] {t['title']}: {summary}")
        team = set(projects.get(slug).get("team", []))
        known = {x["title"].strip().lower() for x in projects.read_tasks(slug)}
        for title, who in TAG_NEW_TASK.findall(text):
            if title.strip().lower() in known:
                continue   # the same follow-up mentioned twice, or already on the board
            known.add(title.strip().lower())
            new = projects.add_task(slug, title.strip(), who if who in team else None)
            if new:
                projects.log_change(slug, aid, f"proposed {new}: {title.strip()}")
    bus.publish({"type": "board", "slug": slug})
    bus.publish({"type": "changelog", "slug": slug, "entry": c})
    return run


# ---------------------------------------------------------------- sprints / autopilot

def sprint_state(slug):
    with _lock:
        return dict(SPRINTS.get(slug, {"running": False}))


def start_sprint(slug, autopilot=False):
    from . import principal  # late import (principal uses runner)
    with _lock:
        if SPRINTS.get(slug, {}).get("running"):
            raise ValueError("A sprint is already running for this project.")
        SPRINTS[slug] = {"running": True, "stop": False, "cycle": 0, "autopilot": autopilot,
                         "cycles": settings()["autopilot_cycles"] if autopilot else 1, "note": "Principal is planning…"}
    bus.publish({"type": "sprint", "slug": slug, "sprint": sprint_state(slug)})

    def loop():
        try:
            sp = SPRINTS[slug]
            for cycle in range(1, sp["cycles"] + 1):
                if sp["stop"]:
                    break
                sp.update(cycle=cycle, note="Principal is planning…")
                bus.publish({"type": "sprint", "slug": slug, "sprint": sprint_state(slug)})
                plan = principal.plan_sprint(slug)
                if sp["stop"]:
                    break
                assign = plan.get("assign", [])
                sp["note"] = plan.get("note") or ""
                if not assign:
                    sp["note"] = plan.get("note") or "Nothing left to assign."
                    break
                bus.publish({"type": "sprint", "slug": slug, "sprint": sprint_state(slug)})
                limit = threading.Semaphore(settings()["max_parallel"])

                def go(x):
                    with limit:
                        if not sp["stop"]:
                            work_task(slug, x["agent"], x["task"])

                threads = [threading.Thread(target=go, args=(x,), daemon=True) for x in assign]
                for th in threads:
                    th.start()
                for th in threads:
                    th.join()
                if not [t for t in projects.read_tasks(slug) if t["column"] == "Up Next"]:
                    if cycle < sp["cycles"]:
                        continue   # the Principal may still add follow-up work
        except Exception as e:  # noqa: BLE001
            SPRINTS[slug]["note"] = f"Sprint error: {type(e).__name__}: {e}"
        finally:
            SPRINTS[slug]["running"] = False
            bus.publish({"type": "sprint", "slug": slug, "sprint": sprint_state(slug)})

    threading.Thread(target=loop, daemon=True).start()
    return sprint_state(slug)


def stop_sprint(slug):
    with _lock:
        if slug in SPRINTS:
            SPRINTS[slug]["stop"] = True
            SPRINTS[slug]["note"] = "Stopping…"
    for aid in projects.get(slug).get("team", []):
        stop_agent(aid)
    p = agents.principal()
    if p and snap(p["id"]).get("project") == slug:
        stop_agent(p["id"])
    bus.publish({"type": "sprint", "slug": slug, "sprint": sprint_state(slug)})
