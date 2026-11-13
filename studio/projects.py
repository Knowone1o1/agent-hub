"""Projects: projects/<slug>/ is the agents' working directory; .project-cache/ holds the shared memory.

  CONTEXT.md     brief, architecture decisions, tech stack, shared knowledge (agents may add decisions)
  STATUS.md      the Kanban board (Up Next / In Progress / Done), owned by the studio, edited via the UI or tags
  CHANGELOG.log  one line per thing an agent built or changed
"""
import json
import re
import shutil
import threading
import time
from pathlib import Path

from . import PROJECTS_DIR, TEMPLATES_DIR, agents

_lock = threading.RLock()
COLUMNS = ("Up Next", "In Progress", "Done")
LINE = re.compile(r"^\s*-\s*\[(T\d+)\]\s*(.+?)\s*(?:\(@([\w-]+)\))?\s*$")


def slugify(s):
    return agents.slugify(s, "project")


def pdir(slug):
    p = PROJECTS_DIR / slug
    if not (p / "project.json").exists():
        raise KeyError(slug)
    return p


def cache(slug):
    return pdir(slug) / ".project-cache"


def _read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(p, text):
    p = Path(p)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for _ in range(20):
        try:
            tmp.replace(p)
            return
        except PermissionError:
            time.sleep(0.05)
    p.write_text(text, encoding="utf-8")


def all_projects():
    out = []
    for d in sorted(PROJECTS_DIR.iterdir()) if PROJECTS_DIR.exists() else []:
        meta = _read_json(d / "project.json")
        if meta:
            out.append(meta)
    return sorted(out, key=lambda p: p.get("created", ""))


def get(slug):
    meta = _read_json(pdir(slug) / "project.json")
    if meta is None:
        raise KeyError(slug)
    return meta


def save(meta):
    with _lock:
        _write(PROJECTS_DIR / meta["slug"] / "project.json", json.dumps(meta, indent=2, ensure_ascii=False))
    return meta


def assignments():
    """agent id -> project slug, for every agent on an active project team (one project per agent)."""
    out = {}
    for p in all_projects():
        if p.get("state", "active") == "active":
            for aid in p.get("team", []):
                out.setdefault(aid, p["slug"])
    return out


def available_agents(exclude_slug=None):
    taken = assignments()
    return [a for a in agents.all_agents() if a["kind"] != "principal" and taken.get(a["id"], exclude_slug) == exclude_slug]


def create(name, brief, team, tasks, principal_summary=""):
    with _lock:
        base = slugify(name)
        slug, n = base, 2
        while (PROJECTS_DIR / slug).exists():
            slug, n = f"{base}-{n}", n + 1
        root = PROJECTS_DIR / slug
        shutil.copytree(TEMPLATES_DIR / "project", root)
        taken = assignments()
        clash = [a for a in team if a in taken]
        if clash:
            shutil.rmtree(root, ignore_errors=True)
            raise ValueError(f"Already on another project: {', '.join(clash)}")
        meta = {"slug": slug, "name": name[:80], "created": time.strftime("%Y-%m-%d %H:%M:%S"), "state": "active",
                "team": list(dict.fromkeys(team)), "next_task": 1, "cost_usd": 0.0, "runs": 0}
        save(meta)
        c = root / ".project-cache"
        ctx = (c / "CONTEXT.md").read_text(encoding="utf-8")
        ctx = ctx.replace("{{NAME}}", name).replace("{{DATE}}", time.strftime("%Y-%m-%d")) \
                 .replace("{{BRIEF}}", brief.strip()).replace("{{PLAN}}", principal_summary.strip() or "_To be decided._")
        _write(c / "CONTEXT.md", ctx)
        write_tasks(slug, [])
        for t in tasks:
            add_task(slug, t.get("title", ""), t.get("assignee"))
        log_change(slug, "principal", f"Project created with team: {', '.join(meta['team']) or '(none)'}")
        return get(slug)


# ---------------------------------------------------------------- STATUS.md (Kanban)

def read_tasks(slug):
    p = cache(slug) / "STATUS.md"
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    tasks, col = [], None
    for line in text.splitlines():
        h = re.match(r"^##\s+(.+?)\s*$", line)
        if h:
            name = h.group(1).strip().lower()
            col = next((c for c in COLUMNS if c.lower() == name), None)
            continue
        m = LINE.match(line)
        if m and col:
            title = m.group(2).strip()
            note = ""
            if " — " in title:
                title, note = title.split(" — ", 1)
            tasks.append({"id": m.group(1), "title": title.strip(), "assignee": m.group(3), "column": col, "note": note.strip()})
    return tasks


def write_tasks(slug, tasks):
    meta = get(slug)
    lines = [f"# Status: {meta['name']}", "", "_Live board. The studio keeps this file in sync with the Kanban in the UI._", ""]
    for col in COLUMNS:
        lines.append(f"## {col}")
        for t in [t for t in tasks if t["column"] == col]:
            note = f" — {t['note']}" if t.get("note") else ""
            who = f" (@{t['assignee']})" if t.get("assignee") else ""
            lines.append(f"- [{t['id']}] {t['title']}{note}{who}")
        lines.append("")
    with _lock:
        _write(cache(slug) / "STATUS.md", "\n".join(lines))


def add_task(slug, title, assignee=None, column="Up Next"):
    with _lock:
        title = re.sub(r"[\[\]\n]", " ", title or "").strip()[:160]
        if not title:
            return None
        meta = get(slug)
        tid = f"T{meta.get('next_task', 1)}"
        meta["next_task"] = meta.get("next_task", 1) + 1
        save(meta)
        tasks = read_tasks(slug)
        tasks.append({"id": tid, "title": title, "assignee": assignee or None, "column": column, "note": ""})
        write_tasks(slug, tasks)
        return tid


def update_task(slug, tid, **fields):
    with _lock:
        tasks = read_tasks(slug)
        for t in tasks:
            if t["id"] == tid:
                for k, v in fields.items():
                    if k in ("title", "assignee", "column", "note") and (k != "column" or v in COLUMNS):
                        t[k] = v
        write_tasks(slug, tasks)
        return next((t for t in tasks if t["id"] == tid), None)


def delete_task(slug, tid):
    with _lock:
        write_tasks(slug, [t for t in read_tasks(slug) if t["id"] != tid])


# ---------------------------------------------------------------- CHANGELOG.log

def log_change(slug, who, text):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {who} | {text.strip().replace(chr(10), ' ')[:500]}\n"
    with _lock, open(cache(slug) / "CHANGELOG.log", "a", encoding="utf-8") as f:
        f.write(line)
    return parse_change(line)


def parse_change(line):
    parts = line.rstrip("\n").split(" | ", 2)
    return {"t": parts[0], "who": parts[1], "text": parts[2]} if len(parts) == 3 else None


def changelog(slug, last=60):
    try:
        lines = (cache(slug) / "CHANGELOG.log").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [c for c in (parse_change(l) for l in lines if " | " in l) if c][-last:]


def context_text(slug):
    try:
        return (cache(slug) / "CONTEXT.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def status_text(slug):
    try:
        return (cache(slug) / "STATUS.md").read_text(encoding="utf-8")
    except OSError:
        return ""
