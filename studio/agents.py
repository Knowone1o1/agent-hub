"""Agent characters live in .agents/<id>.md: a small front-matter header plus a free-form bio/personality body.

Deletion is user-only: nothing in the Principal or agent code paths calls delete(); only the user's DELETE route does,
and even then the file is moved to .agents/.trash (recoverable), never erased.
"""
import re
import shutil
import threading
import time

from . import AGENTS_DIR

_lock = threading.Lock()
FIELDS = ("id", "name", "role", "avatar", "color", "model", "tone", "skills", "look", "kind", "created", "created_by")
# look (optional): "hat: beret, outfit: smock, hair: bob, hairColor: #7a5cff, skin: 0-5, eyes: round|happy|wide|lashes,
#   mouth: smile|grin|smirk|open, glasses: none|round|square|monocle, extra: none|freckles|blush|mustache|beard"
PALETTE = ["#f472b6", "#60a5fa", "#34d399", "#fbbf24", "#a78bfa", "#f87171", "#22d3ee", "#fb923c", "#4ade80", "#e879f9"]
MODELS = ("sonnet", "opus", "haiku")


def slugify(s, fallback="agent"):
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:40] or fallback


def parse(text, fallback_id):
    meta, body = {}, text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip().strip('"')
        body = m.group(2)
    meta.setdefault("id", fallback_id)
    meta.setdefault("name", fallback_id.title())
    meta.setdefault("role", "Specialist")
    meta.setdefault("avatar", "🤖")
    meta.setdefault("color", "#60a5fa")
    meta.setdefault("model", "sonnet")
    meta.setdefault("tone", "clear and friendly")
    meta.setdefault("kind", "member")
    meta["skills"] = [s.strip() for s in meta.get("skills", "").split(",") if s.strip()]
    meta["bio"] = body.strip()
    return meta


def render(a):
    head = "\n".join(f"{k}: {', '.join(a[k]) if k == 'skills' else a[k]}" for k in FIELDS if a.get(k) not in (None, "", []))
    return f"---\n{head}\n---\n\n{a.get('bio', '').strip()}\n"


def all_agents():
    out = []
    for p in sorted(AGENTS_DIR.glob("*.md")):
        try:
            out.append(parse(p.read_text(encoding="utf-8"), p.stem))
        except OSError:
            continue
    order = {"principal": 0}
    return sorted(out, key=lambda a: (order.get(a["kind"], 1), a.get("created", ""), a["name"]))


def get(aid):
    p = AGENTS_DIR / f"{aid}.md"
    if not p.exists():
        raise KeyError(aid)
    return parse(p.read_text(encoding="utf-8"), aid)


def principal():
    return next((a for a in all_agents() if a["kind"] == "principal"), None)


def create(spec, created_by="principal"):
    """Write a new character profile. Returns the agent dict (id made unique)."""
    with _lock:
        base = slugify(spec.get("id") or spec.get("name"))
        aid, n = base, 2
        while (AGENTS_DIR / f"{aid}.md").exists():
            aid, n = f"{base}-{n}", n + 1
        used = {a["color"] for a in all_agents()}
        color = spec.get("color") if re.match(r"^#[0-9a-fA-F]{6}$", spec.get("color") or "") else \
            next((c for c in PALETTE if c not in used), PALETTE[len(used) % len(PALETTE)])
        bio = spec.get("bio") or "\n\n".join(x for x in (
            f"**Personality.** {spec.get('personality', '').strip()}" if spec.get("personality") else "",
            f"**Why I was recruited.** {spec.get('why', '').strip()}" if spec.get("why") else "",
            f"**How I work.** {spec.get('instructions', '').strip()}" if spec.get("instructions") else "") if x)
        skills = spec.get("skills") or []
        if isinstance(skills, str):
            skills = [s.strip() for s in skills.split(",")]
        a = {"id": aid, "name": (spec.get("name") or aid.title())[:40], "role": (spec.get("role") or "Specialist")[:60],
             "avatar": (spec.get("avatar") or "🤖")[:4], "color": color,
             "model": spec.get("model") if spec.get("model") in MODELS else "sonnet",
             "tone": (spec.get("tone") or "clear and friendly")[:160], "skills": [s for s in skills if s][:8],
             "look": re.sub(r"[\n\r]", " ", str(spec.get("look") or ""))[:200],
             "kind": "member", "created": time.strftime("%Y-%m-%d %H:%M"), "created_by": created_by, "bio": bio}
        (AGENTS_DIR / f"{aid}.md").write_text(render(a), encoding="utf-8")
        return a


def update(aid, fields):
    with _lock:
        a = get(aid)
        for k in ("name", "role", "avatar", "color", "model", "tone", "bio"):
            if k in fields and fields[k] not in (None, ""):
                a[k] = fields[k]
        if a["model"] not in MODELS:
            a["model"] = "sonnet"
        (AGENTS_DIR / f"{aid}.md").write_text(render(a), encoding="utf-8")
        return a


def delete_by_user(aid):
    """USER-ONLY. Moves the profile to .agents/.trash so it can be restored by hand."""
    with _lock:
        p = AGENTS_DIR / f"{aid}.md"
        if not p.exists():
            raise KeyError(aid)
        if get(aid)["kind"] == "principal":
            raise ValueError("The Principal can't be deleted.")
        trash = AGENTS_DIR / ".trash"
        trash.mkdir(exist_ok=True)
        shutil.move(str(p), str(trash / f"{aid}-{int(time.time())}.md"))
