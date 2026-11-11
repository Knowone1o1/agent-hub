"""
Agent Hub - a local workspace where you and several AI agents share one conversation.

Run:  py server.py      then open http://127.0.0.1:8765
No pip installs needed (Python standard library only).

Agents are defined in agents.json. Each agent has a "type" that picks an adapter:
  claude_cli         - Claude via your Claude Code login (subscription, no API bill)
  manual             - copy/paste relay for any chat app (ChatGPT, Gemini, ...)
  codex_cli          - OpenAI Codex CLI, signed in with your ChatGPT plan
  openai_compatible  - any OpenAI-style HTTP endpoint, e.g. a free local Ollama model
To add a new kind of bot, write one function and register it in ADAPTERS.
"""

import json
import lab
import mimetypes
import os
import urllib.parse
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
ROOMS = DATA / "rooms"
SHARED = ROOT / "shared"          # a folder agents with file tools can work in
STATIC = ROOT / "static"
CONFIG_FILE = ROOT / "agents.json"
PROFILE_FILE = ROOT / "profile.md"
HOST, PORT = "127.0.0.1", 8765

for d in (DATA, ROOMS, SHARED):
    d.mkdir(parents=True, exist_ok=True)

LOCK = threading.RLock()
RUNNING = set()  # room ids with an active worker thread


# ---------------------------------------------------------------- config

def load_config():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)


def slug(name, taken):
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "agent"
    sid, n = base, 2
    while sid in taken:
        sid, n = f"{base}-{n}", n + 1
    return sid


AGENT_TYPES = ("claude_cli", "manual", "codex_cli", "openai_compatible")
AGENT_FIELDS = ("name", "type", "enabled", "roles", "color", "description", "persona", "model",
                "can_edit_files", "web", "base_url", "api_key_env", "command")


def upsert_agent(data):
    with LOCK:
        cfg = load_config()
        name = (data.get("name") or "").strip()
        if not name or not re.fullmatch(r"[\w][\w-]{0,30}", name):
            raise ValueError("Name must be one word (letters, numbers, - or _), so you can @mention it.")
        if data.get("type") not in AGENT_TYPES:
            raise ValueError("Unknown engine.")
        others = [a for a in cfg["agents"] if a["id"] != data.get("id")]
        if any(a["name"].lower() == name.lower() for a in others):
            raise ValueError(f"There is already an agent called {name}.")
        existing = next((a for a in cfg["agents"] if a["id"] == data.get("id")), None)
        agent = existing if existing is not None else {"id": slug(name, {a["id"] for a in cfg["agents"]})}
        for k in AGENT_FIELDS:
            if k in data:
                v = data[k]
                if v in ("", None) and k not in ("enabled", "can_edit_files", "web"):
                    agent.pop(k, None)
                else:
                    agent[k] = v
        if agent.get("can_edit_files") is not None:  # the old hand-written flag is now a checkbox
            agent["extra_args"] = [x for x in agent.get("extra_args", []) if x not in ("--permission-mode", "acceptEdits")]
        if existing is None:
            cfg["agents"].append(agent)
        save_config(cfg)
        return agent


def agents_by_id():
    return {a["id"]: a for a in load_config()["agents"] if a.get("enabled", True)}


def user_name():
    return load_config().get("user_name", "Me")


def read_profile():
    return PROFILE_FILE.read_text(encoding="utf-8") if PROFILE_FILE.exists() else ""


# ---------------------------------------------------------------- rooms

def room_path(rid):
    if not re.fullmatch(r"[a-f0-9]{12}", rid):
        raise KeyError(rid)
    return ROOMS / f"{rid}.json"


def load_room(rid):
    with open(room_path(rid), encoding="utf-8") as f:
        return json.load(f)


def save_room(room):
    room["updated"] = time.time()
    tmp = room_path(room["id"]).with_suffix(".tmp")
    tmp.write_text(json.dumps(room, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, room_path(room["id"]))


def new_message(author, kind, text, status="done"):
    return {"id": uuid.uuid4().hex[:10], "author": author, "kind": kind,
            "text": text, "status": status, "ts": time.time()}


# ---------------------------------------------------------------- prompts

def display_name(author_id, agents):
    if author_id == "user":
        return user_name()
    return agents.get(author_id, {}).get("name", author_id)


def system_text(agent, room, agents):
    if room.get("kind") == "chat":
        profile = read_profile().strip() or "(no profile written yet)"
        return f"""You are {agent['name']}, chatting one-on-one with {user_name()} in their personal AI app.
{agent.get('persona', '')}
Write only your reply, with no "{agent['name']}:" prefix.

What we know about {user_name()} (shared profile, written by the user):
{profile}"""
    others = [agents[m] for m in room["members"] if m in agents and m != agent["id"]]
    roster = "\n".join(f"- @{a['name']}: {a.get('description', '')}" for a in others) or "- (none yet)"
    profile = read_profile().strip() or "(no profile written yet)"
    mgr = agents.get(room.get("manager") or "")
    if mgr and mgr["id"] == agent["id"]:
        role = f"""
YOU ARE THE TEAM MANAGER. {user_name()} gives the goal; you run the team:
- Break the goal into concrete tasks and assign them by writing @Name followed by clear instructions.
  Only the agents you @mention will act, in the order you mention them (later ones see earlier ones' output).
- After they report back you get their results. Review them critically: assign fixes or next steps
  with new @mentions, or, when the work is good enough, write a final answer to {user_name()}
  WITHOUT @mentioning any agent (that ends the round and hands control back to {user_name()}).
- Ask {user_name()} directly when you need a decision only they can make (and @mention no agent).
- Use what you know about {user_name()} to steer the work toward what they actually want."""
    elif mgr:
        role = f"""
@{mgr['name']} is the team manager. Do the task the manager (or {user_name()}) assigned to you,
then report back clearly: what you did, the result, and any open questions or risks.
Don't do other agents' assignments. If the task is unclear, make a sensible assumption and say so."""
    else:
        role = """
- Everyone sees the whole conversation. Build on what others said instead of repeating it.
- Disagree openly when you think someone (including another AI) is wrong, and say why.
- Address someone directly with @Name. Divide up work when it helps."""
    return f"""You are {agent['name']}, one participant in a shared group workspace called "{room['title']}".
The human is {user_name()}. The other AI participants are:
{roster}

How this room works:{role}
- Keep it focused and reasonably concise. Write only your own message, with no "{agent['name']}:" prefix.
{agent.get('persona', '')}

What we know about {user_name()} (shared profile, written by the user):
{profile}"""


def transcript(messages, agents):
    lines = []
    for m in messages:
        if m["status"] != "done" or not m["text"].strip():
            continue
        lines.append(f"[{display_name(m['author'], agents)}]: {m['text'].strip()}")
    return "\n\n".join(lines)


def full_prompt(agent, room, agents, upto):
    history = transcript(room["messages"][:upto], agents)
    return (f"{system_text(agent, room, agents)}\n\n=== Conversation so far ===\n\n{history}\n\n"
            f"=== Your turn ===\nWrite {agent['name']}'s next message.")


def relay_prompt(agent, room, agents, upto):
    """For copy/paste agents: send only what is new since this agent last spoke,
    so the user can keep one ongoing chat per room in the other app."""
    msgs = room["messages"][:upto]
    last = max((i for i, m in enumerate(msgs) if m["author"] == agent["id"] and m["status"] == "done"),
               default=None)
    if room.get("kind") == "chat":
        # One-on-one with a chat app: it already knows you, so just pass your words along.
        start = 0 if last is None else last + 1
        return "\n\n".join(m["text"] for m in msgs[start:] if m["author"] == "user" and m["status"] == "done")
    if last is None:
        return full_prompt(agent, room, agents, upto)
    new = transcript(msgs[last + 1:], agents)
    reminder = ""
    if room.get("manager") == agent["id"]:
        names = ", ".join(f"@{agents[m]['name']}" for m in room["members"] if m in agents and m != agent["id"])
        reminder = (f"\nYou are still the team manager ({names}). Assign next steps with @mentions, "
                    f"or give {user_name()} the final answer without @mentioning any agent.")
    return (f"[Agent Hub, room \"{room['title']}\"] New messages since your last reply:\n\n{new}\n{reminder}\n"
            f"Reply as {agent['name']} (just your message).")


# ---------------------------------------------------------------- adapters

def run_claude_cli(agent, prompt):
    exe = agent.get("command") or shutil.which("claude")
    if not exe:
        raise RuntimeError("Claude Code CLI not found. Install Claude Code or set 'command' in agents.json.")
    args = [exe, "-p", "--output-format", "text"]
    if agent.get("model"):
        args += ["--model", agent["model"]]
    if agent.get("can_edit_files"):
        args += ["--permission-mode", "acceptEdits"]
    args += agent.get("extra_args", [])
    if agent.get("web"):
        args += ["--allowedTools", "WebSearch", "WebFetch"]  # variadic flag: keep it last
    r = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8",
                       cwd=str(SHARED), timeout=agent.get("timeout", 600))
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "claude exited with an error").strip()[-1500:])
    return r.stdout.strip()


def run_codex_cli(agent, prompt):
    exe = agent.get("command") or shutil.which("codex")
    if not exe:
        raise RuntimeError("Codex CLI not found. See README: 'Adding ChatGPT automatically (Codex)'.")
    out = Path(tempfile.gettempdir()) / f"agenthub-codex-{uuid.uuid4().hex}.txt"
    args = [exe, "exec", "--skip-git-repo-check", "--output-last-message", str(out)]
    if agent.get("model"):
        args += ["--model", agent["model"]]
    args += agent.get("extra_args", []) + ["-"]
    try:
        r = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           cwd=str(SHARED), timeout=agent.get("timeout", 600))
        if out.exists() and out.read_text(encoding="utf-8").strip():
            return out.read_text(encoding="utf-8").strip()
        raise RuntimeError((r.stderr or r.stdout or "codex returned nothing").strip()[-1500:])
    finally:
        out.unlink(missing_ok=True)


def run_openai_compatible(agent, prompt):
    url = agent["base_url"].rstrip("/") + "/chat/completions"
    body = json.dumps({"model": agent["model"],
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    headers = {"Content-Type": "application/json"}
    key = os.environ.get(agent["api_key_env"]) if agent.get("api_key_env") else None
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=agent.get("timeout", 600)) as resp:
        return json.load(resp)["choices"][0]["message"]["content"].strip()


ADAPTERS = {
    "claude_cli": run_claude_cli,
    "codex_cli": run_codex_cli,
    "openai_compatible": run_openai_compatible,
    # "manual" is handled by the worker: it pauses and waits for a pasted reply.
}


# ---------------------------------------------------------------- turn-taking worker

def kick(rid):
    with LOCK:
        if rid in RUNNING:
            return
        RUNNING.add(rid)
    threading.Thread(target=worker, args=(rid,), daemon=True).start()


def worker(rid):
    try:
        while True:
            with LOCK:
                room = load_room(rid)
                agents = agents_by_id()
                if any(m["status"] == "waiting" for m in room["messages"]):
                    return  # paused on a copy/paste relay
                if not room["queue"]:
                    return
                aid = room["queue"].pop(0)
                agent = agents.get(aid)
                if not agent or aid not in room["members"]:
                    save_room(room)
                    continue
                upto = len(room["messages"])
                msg = new_message(aid, "agent", "", status="thinking")
                if agent["type"] == "manual":
                    msg["status"] = "waiting"
                    msg["relay_prompt"] = relay_prompt(agent, room, agents, upto)
                    room["messages"].append(msg)
                    save_room(room)
                    return
                prompt = full_prompt(agent, room, agents, upto)
                room["messages"].append(msg)
                save_room(room)

            try:
                fn = ADAPTERS.get(agent["type"])
                if not fn:
                    raise RuntimeError(f"Unknown agent type '{agent['type']}'")
                text, status = fn(agent, prompt), "done"
            except Exception as e:  # noqa: BLE001 - report any adapter failure in the chat
                text, status = f"{type(e).__name__}: {e}", "error"

            with LOCK:
                room = load_room(rid)
                for m in room["messages"]:
                    if m["id"] == msg["id"]:
                        m.update(text=text, status=status, ts=time.time())
                        if status == "done":
                            after_reply(room, m, agents_by_id())
                save_room(room)
    finally:
        with LOCK:
            RUNNING.discard(rid)


def mentioned(text, room, agents, exclude=()):
    """Members @mentioned in text, in the order they first appear."""
    hits = []
    for aid in room["members"]:
        a = agents.get(aid)
        if not a or aid in exclude:
            continue
        m = re.search(rf"@{re.escape(a['name'])}(?![\w-])", text, re.I)
        if m:
            hits.append((m.start(), aid))
    return [aid for _, aid in sorted(hits)]


def after_reply(room, msg, agents):
    """Manager hand-off: when the manager @mentions workers, queue them and then the manager
    again so it can review their results. A manager reply with no @mentions ends the cycle."""
    mgr = room.get("manager")
    if not mgr or msg["author"] != mgr or room.get("handoffs_left", 0) <= 0:
        return
    workers = mentioned(msg["text"], room, agents, exclude=(mgr,))
    if workers:
        room["handoffs_left"] -= 1
        room["queue"] = workers + [mgr] + room["queue"]


# ---------------------------------------------------------------- dashboard data

def overview():
    """Everything the wall dashboard needs in one call."""
    agents = agents_by_id()
    rooms = []
    for f in ROOMS.glob("*.json"):
        r = json.loads(f.read_text(encoding="utf-8"))
        msgs = r["messages"]
        waiting = next((m for m in msgs if m["status"] == "waiting"), None)
        thinking = next((m for m in msgs if m["status"] == "thinking"), None)
        state = ("waiting" if waiting else "working" if thinking or r["queue"] or r["id"] in RUNNING else "idle")
        members = []
        for aid in r["members"]:
            a = agents.get(aid)
            if not a:
                continue
            mine = [m for m in msgs if m["author"] == aid]
            last = mine[-1] if mine else None
            ast = "idle"
            if last and last["status"] in ("thinking", "waiting", "error"):
                ast = last["status"]
            elif aid in r["queue"]:
                ast = "queued"
            members.append({"id": aid, "name": a["name"], "color": a.get("color"), "type": a["type"],
                            "description": a.get("description", ""), "state": ast,
                            "is_manager": aid == r.get("manager"),
                            "replies": sum(1 for m in mine if m["status"] == "done"),
                            "last_text": (last or {}).get("text", "")[:280],
                            "last_ts": (last or {}).get("ts")})
        feed = [{"author": m["author"],
                 "name": user_name() if m["author"] == "user" else agents.get(m["author"], {}).get("name", m["author"]),
                 "color": agents.get(m["author"], {}).get("color"), "status": m["status"],
                 "text": m["text"][:200], "ts": m["ts"]} for m in msgs[-14:]]
        rooms.append({"id": r["id"], "title": r["title"], "state": state, "updated": r.get("updated", 0),
                      "manager": r.get("manager"), "queue": r["queue"], "handoffs_left": r.get("handoffs_left", 0),
                      "max_handoffs": r.get("max_handoffs", 3), "message_count": len(msgs),
                      "waiting_on": agents.get(waiting["author"], {}).get("name") if waiting else None,
                      "working_on": agents.get(thinking["author"], {}).get("name") if thinking else None,
                      "members": members, "feed": feed})
    rooms.sort(key=lambda r: ({"waiting": 0, "working": 1, "idle": 2}[r["state"]], -r["updated"]))
    files = []
    for f in SHARED.rglob("*"):
        if f.is_file() and not any(part.startswith(".") for part in f.relative_to(SHARED).parts):
            st = f.stat()
            files.append({"path": f.relative_to(SHARED).as_posix(), "size": st.st_size, "mtime": st.st_mtime})
    files.sort(key=lambda x: -x["mtime"])
    return {"user_name": user_name(), "now": time.time(), "rooms": rooms, "files": files[:40]}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, data, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        try:
            p = self.path.split("?")[0]
            m = re.fullmatch(r"/api/lab/studies/([a-z0-9-]+)/export/([a-z.]+)", p)
            if m:
                import studies
                name, ctype, text = studies.export(m.group(1), m.group(2))
                data = text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", f"{ctype}; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if p.startswith("/api/lab/"):
                q = dict(urllib.parse.parse_qsl(self.path.split("?", 1)[1])) if "?" in self.path else {}
                obj, code = lab.handle("GET", p[len("/api/lab/"):].strip("/").split("/"), None, q)
                return self.send_json(obj, code)
            if p in ("/", "/index.html", "/dashboard", "/lab"):
                page = {"/dashboard": "dashboard.html", "/lab": "lab.html"}.get(p, "index.html")
                self.send_bytes((STATIC / page).read_bytes(), "text/html; charset=utf-8")
            elif p.startswith("/static/") and p.rsplit(".", 1)[-1] in ("png", "ico"):
                f = (STATIC / p[len("/static/"):]).resolve()
                if STATIC.resolve() not in f.parents or not f.is_file():
                    raise FileNotFoundError(p)
                self.send_bytes(f.read_bytes(), "image/png" if f.suffix == ".png" else "image/x-icon")
            elif p == "/api/overview":
                self.send_json(overview())
            elif p.startswith("/files/"):
                rel = urllib.parse.unquote(p[len("/files/"):])
                f = (SHARED / rel).resolve()
                if SHARED.resolve() not in f.parents or not f.is_file():
                    raise FileNotFoundError(rel)
                ctype = mimetypes.guess_type(f.name)[0] or "text/plain"
                if ctype.startswith("text/") or f.suffix in (".md", ".csv", ".json", ".py", ".js"):
                    ctype = "text/plain; charset=utf-8" if not ctype.startswith("text/html") else ctype
                self.send_bytes(f.read_bytes(), ctype)
            elif p == "/api/state":
                cfg = load_config()
                rooms = []
                for f in ROOMS.glob("*.json"):
                    r = json.loads(f.read_text(encoding="utf-8"))
                    rooms.append({"id": r["id"], "title": r["title"], "updated": r.get("updated", 0),
                                  "kind": r.get("kind", "project"), "members": r["members"],
                                  "waiting": any(m["status"] == "waiting" for m in r["messages"]),
                                  "busy": r["id"] in RUNNING or bool(r["queue"])})
                rooms.sort(key=lambda r: -r["updated"])
                agents_out = []
                for a in cfg["agents"]:
                    o = {k: a.get(k) for k in ("id",) + AGENT_FIELDS}
                    if o.get("can_edit_files") is None:
                        o["can_edit_files"] = "acceptEdits" in a.get("extra_args", [])
                    agents_out.append(o)
                self.send_json({"user_name": cfg.get("user_name", "Me"),
                                "default_manager": cfg.get("default_manager"),
                                "agents": agents_out, "teams": cfg.get("teams", []),
                                "claude_found": bool(shutil.which("claude")),
                                "codex_found": bool(shutil.which("codex")),
                                "rooms": rooms})
            elif p.startswith("/api/rooms/"):
                room = load_room(p.split("/")[3])
                room["busy"] = room["id"] in RUNNING or bool(room["queue"])
                self.send_json(room)
            elif p == "/api/profile":
                self.send_json({"text": read_profile()})
            else:
                self.send_json({"error": "not found"}, 404)
        except (KeyError, FileNotFoundError):
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            p = self.path.split("?")[0].strip("/").split("/")
            b = self.body()
            if p[:2] == ["api", "lab"]:
                obj, code = lab.handle("POST", p[2:], b, {})
                return self.send_json(obj, code)
            agents = agents_by_id()
            if p == ["api", "rooms"]:
                rid = uuid.uuid4().hex[:12]
                members = [m for m in b.get("members", []) if m in agents] or list(agents)
                kind = "chat" if b.get("kind") == "chat" else "project"
                if kind == "chat":
                    members = members[:1]
                manager = b.get("manager") if kind == "project" and b.get("manager") in members else None
                default_title = f"Chat with {agents[members[0]]['name']}" if kind == "chat" else "New project"
                room = {"id": rid, "kind": kind, "title": (b.get("title") or default_title).strip()[:80],
                        "members": members, "manager": manager, "max_handoffs": 3,
                        "messages": [], "queue": [], "created": time.time()}
                save_room(room)
                return self.send_json(room)
            if p == ["api", "shutdown"]:
                self.send_json({"ok": True})
                threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
                return
            if p == ["api", "agents", "save"]:
                try:
                    return self.send_json({"ok": True, "agent": upsert_agent(b)})
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
            if p == ["api", "agents", "delete"]:
                with LOCK:
                    cfg = load_config()
                    cfg["agents"] = [a for a in cfg["agents"] if a["id"] != b.get("id")]
                    for t in cfg.get("teams", []):
                        t["members"] = [m for m in t["members"] if m != b.get("id")]
                    save_config(cfg)
                return self.send_json({"ok": True})
            if p == ["api", "teams", "save"]:
                with LOCK:
                    cfg = load_config()
                    teams = cfg.setdefault("teams", [])
                    ids = {a["id"] for a in cfg["agents"]}
                    name = (b.get("name") or "").strip()[:60]
                    if not name:
                        return self.send_json({"error": "Give the team a name."}, 400)
                    members = [m for m in b.get("members", []) if m in ids]
                    team = next((t for t in teams if t["id"] == b.get("id")), None)
                    if team is None:
                        team = {"id": slug(name, {t["id"] for t in teams})}
                        teams.append(team)
                    team.update(name=name, members=members,
                                manager=b.get("manager") if b.get("manager") in members else None,
                                description=(b.get("description") or "").strip()[:300])
                    save_config(cfg)
                return self.send_json({"ok": True, "team": team})
            if p == ["api", "teams", "delete"]:
                with LOCK:
                    cfg = load_config()
                    cfg["teams"] = [t for t in cfg.get("teams", []) if t["id"] != b.get("id")]
                    save_config(cfg)
                return self.send_json({"ok": True})
            if p == ["api", "profile"]:
                PROFILE_FILE.write_text(b.get("text", ""), encoding="utf-8")
                return self.send_json({"ok": True})
            if len(p) >= 4 and p[:2] == ["api", "rooms"]:
                rid, action = p[2], p[3]
                with LOCK:
                    room = load_room(rid)
                    if action == "message":
                        text = (b.get("text") or "").strip()
                        if text:
                            room["messages"].append(new_message("user", "user", text))
                        direct = mentioned(text, room, agents)
                        mgr = room.get("manager") if room.get("manager") in room["members"] else None
                        if mgr and (not direct or mgr in direct):
                            # Manager mode: everything goes through the manager first.
                            room["handoffs_left"] = int(b.get("max_handoffs", room.get("max_handoffs", 3)))
                            room["queue"] += [mgr]
                        else:
                            ask = direct or [a for a in b.get("ask", []) if a in room["members"]]
                            rounds = max(1, min(int(b.get("rounds", 1)), 10))
                            room["queue"] += ask * rounds
                    elif action == "relay":
                        for m in room["messages"]:
                            if m["id"] == b.get("message_id") and m["status"] == "waiting":
                                m.update(text=(b.get("text") or "").strip(), status="done", ts=time.time())
                                m.pop("relay_prompt", None)
                                after_reply(room, m, agents)
                    elif action == "skip":
                        room["messages"] = [m for m in room["messages"]
                                            if not (m["id"] == b.get("message_id") and m["status"] == "waiting")]
                    elif action == "stop":
                        room["queue"] = []
                        room["messages"] = [m for m in room["messages"] if m["status"] != "waiting"]
                    elif action == "settings":
                        if "title" in b:
                            room["title"] = b["title"].strip()[:80] or room["title"]
                        if "members" in b:
                            room["members"] = [m for m in b["members"] if m in agents]
                        if "manager" in b:
                            room["manager"] = b["manager"] if b["manager"] in room["members"] else None
                        if "max_handoffs" in b:
                            room["max_handoffs"] = max(0, min(int(b["max_handoffs"]), 10))
                    elif action == "delete":
                        room_path(rid).unlink()
                        return self.send_json({"ok": True})
                    else:
                        return self.send_json({"error": "unknown action"}, 400)
                    save_room(room)
                kick(rid)
                return self.send_json({"ok": True})
            self.send_json({"error": "not found"}, 404)
        except (KeyError, FileNotFoundError):
            self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    # Resume rooms that were mid-conversation when the server stopped.
    for f in ROOMS.glob("*.json"):
        r = json.loads(f.read_text(encoding="utf-8"))
        r["messages"] = [m for m in r["messages"] if m["status"] != "thinking"]
        save_room(r)
        if r["queue"]:
            kick(r["id"])
    lab.recover_interrupted()
    import studies
    studies.recover()
    print(f"Agent Hub running at http://{HOST}:{PORT}  (Ctrl+C to stop)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
