"""Talk to Sol (the Principal) about a project, at any time.

Each project keeps its conversation in .project-cache/chat-principal.jsonl. Sol answers with full project context,
can read the project's files (read-only tools), and can act on what you ask through a small, safe action set:
add / change / remove tasks, bench or recall agents, record a decision in CONTEXT.md, and start a sprint.
Recruiting a new agent is only ever proposed: it waits for your Approve click. Sol can never delete an agent.
"""
import json
import threading
import time

from . import STATE_DIR, agents, bus, projects, runner
from .claude_cli import parse_json_block

READ_TOOLS = ["Read", "Glob", "Grep"]
_lock = threading.Lock()


def _file(slug):
    return (projects.cache(slug) if slug else STATE_DIR) / "chat-principal.jsonl"


def history(slug, last=200):
    try:
        lines = _file(slug).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for i, l in enumerate(lines):
        try:
            m = json.loads(l)
            m["i"] = i
            out.append(m)
        except json.JSONDecodeError:
            pass
    return out[-last:]


def _append(slug, msg):
    with _lock, open(_file(slug), "a", encoding="utf-8") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    msg = {**msg, "i": len(history(slug, 100000)) - 1}
    bus.publish({"type": "chat", "slug": slug, "message": msg})
    return msg


def _rewrite(slug, msgs):
    with _lock:
        _file(slug).write_text("".join(json.dumps({k: v for k, v in m.items() if k != "i"}, ensure_ascii=False) + "\n" for m in msgs),
                               encoding="utf-8")


ACTIONS_HELP = """You can act on the project. Put any actions in the "actions" list (leave it empty if none are needed):
  {"type": "add_task", "title": "...", "assignee": "<agent id or null>"}
  {"type": "update_task", "task": "T3", "title": "...", "assignee": "<agent id>", "column": "Up Next|In Progress|Done"}
  {"type": "remove_task", "task": "T3"}
  {"type": "bench", "agent": "<agent id on the team>"}          (move to the Sideline; frees them)
  {"type": "recall", "agent": "<agent id on the Sideline>"}     (put them on this team)
  {"type": "note", "text": "..."}                               (record a decision / fact in CONTEXT.md)
  {"type": "start_sprint", "autopilot": false}                  (only if the user asked to get going)
  {"type": "recruit", "name": "...", "role": "...", "avatar": "🧪", "color": "#22d3ee", "model": "sonnet",
   "personality": "...", "tone": "...", "skills": ["..."], "why": "...", "look": "hat: ..., outfit: ..."}
     (a new specialist; the user must approve it, so only propose one when it clearly helps)
Never delete agents (you can't). Don't invent agent ids: use the ones listed."""


def _context(slug):
    meta = projects.get(slug)
    taken = projects.assignments()
    side = [a for a in agents.all_agents() if a["kind"] != "principal" and a["id"] not in taken]
    elsewhere = [a for a in agents.all_agents() if a["kind"] != "principal" and taken.get(a["id"]) not in (None, slug)]
    log = "\n".join(f"{c['t']} {c['who']}: {c['text']}" for c in projects.changelog(slug, 25))
    try:
        files = [p.name for p in sorted((projects.pdir(slug) / "inputs").iterdir()) if p.is_file()]
    except OSError:
        files = []
    sp = runner.sprint_state(slug)
    live = []
    for aid in meta.get("team", []):
        st = runner.snap(aid)
        if st["state"] != "idle":
            live.append(f"- @{aid} is working: {st.get('task') or ''} · {st.get('step') or ''}")
    return f"""PROJECT: {meta['name']} (folder: projects/{slug}; you can read files there with Read/Glob/Grep)
TEAM:
{runner.team_lines(slug)}
ON THE SIDELINE (free): {', '.join(f"@{a['id']} ({a['name']}, {a['role']})" for a in side) or 'nobody'}
ON OTHER PROJECTS (locked): {', '.join(f"@{a['id']}" for a in elsewhere) or 'nobody'}
WORKING RIGHT NOW:
{chr(10).join(live) or '- nobody'}
SPRINT: {'running' if sp.get('running') else 'not running'}{(' · ' + sp['note']) if sp.get('note') else ''}
INPUT FILES (inputs/): {', '.join(files) or 'none'}

.project-cache/CONTEXT.md:
{projects.context_text(slug)[-9000:]}

.project-cache/STATUS.md:
{projects.status_text(slug)[-5000:]}

RECENT CHANGELOG:
{log or '(empty)'}"""


def _apply(slug, actions):
    """Apply Sol's actions safely. Returns (done: [str], pending: [recruit specs])."""
    done, pending = [], []
    meta = projects.get(slug)
    team = set(meta.get("team", []))
    taken = projects.assignments()
    tasks = {t["id"]: t for t in projects.read_tasks(slug)}
    for a in actions[:12]:
        if not isinstance(a, dict):
            continue
        t = a.get("type")
        try:
            if t == "add_task" and a.get("title"):
                who = a.get("assignee") if a.get("assignee") in team else None
                tid = projects.add_task(slug, a["title"], who)
                if tid:
                    done.append(f"➕ added {tid}: {a['title']}" + (f" → @{who}" if who else ""))
            elif t == "update_task" and a.get("task", "").upper() in tasks:
                tid, f = a["task"].upper(), {}
                if a.get("title"):
                    f["title"] = a["title"]
                if a.get("assignee") in team:
                    f["assignee"] = a["assignee"]
                if a.get("column") in projects.COLUMNS:
                    f["column"] = a["column"]
                if f:
                    projects.update_task(slug, tid, **f)
                    done.append(f"✏️ updated {tid}: " + ", ".join(f"{k} → {v}" for k, v in f.items()))
            elif t == "remove_task" and a.get("task", "").upper() in tasks:
                projects.delete_task(slug, a["task"].upper())
                done.append(f"🗑 removed {a['task'].upper()} from the board")
            elif t == "bench" and a.get("agent") in team and not runner.is_busy(a["agent"]):
                meta = projects.get(slug)
                meta["team"] = [x for x in meta["team"] if x != a["agent"]]
                projects.save(meta)
                team.discard(a["agent"])
                done.append(f"🪑 moved @{a['agent']} to the Sideline")
            elif t == "recall" and a.get("agent") and a["agent"] not in taken and agents.get(a["agent"])["kind"] != "principal":
                meta = projects.get(slug)
                meta["team"] = list(dict.fromkeys(meta["team"] + [a["agent"]]))
                projects.save(meta)
                team.add(a["agent"])
                taken[a["agent"]] = slug
                done.append(f"🙋 recalled @{a['agent']} onto the team")
            elif t == "note" and a.get("text"):
                ctx = projects.cache(slug) / "CONTEXT.md"
                text = ctx.read_text(encoding="utf-8")
                line = f"- {time.strftime('%Y-%m-%d')} @principal (from a chat with the user): {a['text'].strip()}"
                if "## Decisions" in text:
                    i = text.index("## Decisions")
                    j = text.find("\n## ", i + 5)
                    text = (text[:j].rstrip() + "\n" + line + "\n" + text[j:]) if j >= 0 else text.rstrip() + "\n" + line + "\n"
                else:
                    text = text.rstrip() + "\n\n## Decisions\n" + line + "\n"
                ctx.write_text(text, encoding="utf-8")
                done.append(f"📝 noted in CONTEXT.md: {a['text'].strip()[:120]}")
            elif t == "start_sprint" and not runner.sprint_state(slug).get("running"):
                pending.append({"kind": "sprint", "autopilot": bool(a.get("autopilot"))})
            elif t == "recruit" and a.get("name"):
                pending.append({"kind": "recruit", "spec": {k: a.get(k) for k in ("name", "role", "avatar", "color", "model", "personality",
                                                                                    "tone", "skills", "why", "look")}})
        except (KeyError, ValueError) as e:
            done.append(f"⚠ couldn't {t}: {e}")
    for d in done:
        projects.log_change(slug, "principal", f"(chat) {d}")
    if done:
        bus.publish({"type": "roster"})
        bus.publish({"type": "board", "slug": slug})
    return done, pending


def send(slug, text):
    """The user wrote to Sol. Runs in the background; replies arrive over SSE."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Write a message first.")
    projects.get(slug)
    user_msg = _append(slug, {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "role": "user", "text": text[:8000]})
    threading.Thread(target=_reply, args=(slug, user_msg), daemon=True).start()
    return user_msg


def _reply(slug, user_msg):
    p = agents.principal()
    waited = 0
    while p and runner.is_busy(p["id"]) and waited < 240:   # Sol is mid-planning: answer right after
        if waited == 0:
            bus.publish({"type": "chat_wait", "slug": slug})
        time.sleep(2)
        waited += 2
    if not p:
        _append(slug, {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "role": "principal", "text": "(No Principal profile found.)"})
        return
    hist = [m for m in history(slug, 30) if m.get("i") != user_msg.get("i")][-16:]
    convo = "\n".join(f"{'USER' if m['role'] == 'user' else 'YOU (Sol)'}: {m['text']}" for m in hist) or "(no earlier messages)"
    system = (f"{runner.persona(p)}\n\nYou are the Principal and you are chatting with the user (your boss) about a project. "
              "Answer like a sharp, friendly lead: direct, specific to this project, short unless detail is asked for. "
              "You may read files in the project folder to answer accurately. While you think, print [STEP: ...] tags "
              "on their own lines. End your reply with one JSON object in a ```json fence: "
              '{"reply": "<your message to the user, Markdown allowed>", "actions": [...]}.\n\n' + ACTIONS_HELP)
    prompt = f"""{_context(slug)}

EARLIER IN THIS CHAT:
{convo}

THE USER JUST SAID:
{user_msg['text']}"""
    run = runner.run_agent(p["id"], prompt, system, projects.pdir(slug), slug=slug, label="Talking with you",
                           tool_list=READ_TOOLS)
    data = parse_json_block(run.final or run.text) or {}
    reply = str(data.get("reply") or "").strip()
    if not reply:   # no JSON: use the plain text, minus tags
        import re
        reply = re.sub(r"\[(STEP|PROGRESS)[^\]]*\]", "", run.final or run.text).strip() or \
            (f"Sorry, I couldn't answer that ({run.error})." if run.error else "…")
    done, pending = _apply(slug, data.get("actions") or []) if isinstance(data.get("actions"), list) else ([], [])
    _append(slug, {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "role": "principal", "text": reply[:12000],
                   "done": done, "pending": pending, "cost_usd": round(run.cost_usd, 4)})


def resolve(slug, index, which, approve):
    """The user approved or declined a pending item (a recruit or a sprint) on one of Sol's messages."""
    msgs = history(slug, 100000)
    m = next((x for x in msgs if x["i"] == index), None)
    if not m or not m.get("pending") or which >= len(m["pending"]):
        raise ValueError("Nothing to approve there.")
    item = m["pending"][which]
    if item.get("resolved"):
        raise ValueError("Already handled.")
    result = "declined"
    if approve and item["kind"] == "recruit":
        a = agents.create(item["spec"], created_by="principal (approved by user in chat)")
        meta = projects.get(slug)
        meta["team"] = list(dict.fromkeys(meta["team"] + [a["id"]]))
        projects.save(meta)
        projects.log_change(slug, "principal", f"recruited @{a['id']} ({a['name']}, {a['role']}) with the user's approval")
        bus.publish({"type": "roster"})
        result = f"recruited @{a['id']}"
    elif approve and item["kind"] == "sprint":
        runner.start_sprint(slug, item.get("autopilot", False))
        result = "sprint started"
    item["resolved"] = result
    _rewrite(slug, msgs)
    bus.publish({"type": "chat_update", "slug": slug, "message": m})
    return m
