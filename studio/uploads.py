"""Files the user drops into a project brief.

They are staged under state/uploads/<token>/ while the brief is being written, shown to the Principal (text files as
excerpts, everything else by name and size), and moved into projects/<slug>/inputs/ when the team is assembled.
Agents then read them with their normal Read tool (it handles text, code, PDFs and images).
"""
import base64
import re
import shutil
import time
import uuid
from pathlib import Path

from . import STATE_DIR, projects

STAGE = STATE_DIR / "uploads"
MAX_FILE = 25 * 1024 * 1024      # 25 MB per file
MAX_TOTAL = 80 * 1024 * 1024     # 80 MB per brief
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm", ".css", ".js",
            ".ts", ".tsx", ".jsx", ".py", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".rb", ".php", ".sql", ".sh",
            ".bat", ".ps1", ".ini", ".toml", ".cfg", ".log", ".rst", ".tex", ".svg", ".kt", ".swift", ".r", ".m"}


def _safe_name(name):
    name = Path(str(name).replace("\\", "/")).name.strip() or "file"
    name = re.sub(r"[^\w.\- ()\[\]]+", "_", name)[:120]
    return name.lstrip(".") or "file"


def _token(token):
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        raise ValueError("Bad upload token.")
    return STAGE / token


def kind_of(path):
    ext = Path(path).suffix.lower()
    if ext in TEXT_EXT:
        return "text"
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        return "image"
    if ext == ".pdf":
        return "pdf"
    return "file"


def listing(token):
    d = _token(token)
    if not d.exists():
        return []
    return [{"name": p.name, "size": p.stat().st_size, "kind": kind_of(p)} for p in sorted(d.iterdir()) if p.is_file()]


def add(token, files):
    """files: [{name, data (base64)}]. Returns (token, listing)."""
    token = token or uuid.uuid4().hex
    d = _token(token)
    d.mkdir(parents=True, exist_ok=True)
    total = sum(p.stat().st_size for p in d.iterdir() if p.is_file())
    for f in files:
        raw = base64.b64decode(str(f.get("data", "")).split(",")[-1] or b"", validate=False)
        if len(raw) > MAX_FILE:
            raise ValueError(f"{f.get('name')} is larger than 25 MB.")
        total += len(raw)
        if total > MAX_TOTAL:
            raise ValueError("Attachments are limited to 80 MB per project brief.")
        name, n = _safe_name(f.get("name")), 2
        stem, suf = Path(name).stem, Path(name).suffix
        while (d / name).exists():
            name, n = f"{stem} ({n}){suf}", n + 1
        (d / name).write_bytes(raw)
    return token, listing(token)


def remove(token, name):
    p = _token(token) / _safe_name(name)
    if p.exists():
        p.unlink()
    return listing(token)


def summary_for_principal(token, budget=24000):
    """What the Principal sees: names, sizes and an excerpt of each text file."""
    if not token:
        return ""
    files = listing(token)
    if not files:
        return ""
    d, out, left = _token(token), [], budget
    for f in files:
        line = f"- inputs/{f['name']} ({f['kind']}, {f['size'] / 1024:.0f} KB)"
        if f["kind"] == "text" and left > 500:
            try:
                txt = (d / f["name"]).read_text(encoding="utf-8", errors="replace")
            except OSError:
                txt = ""
            excerpt = txt[:min(left, 6000)]
            left -= len(excerpt)
            line += f"\n  ```\n{excerpt}\n  ```" + ("\n  (excerpt; the full file is in the project)" if len(txt) > len(excerpt) else "")
        out.append(line)
    return "\n".join(out)


def attach_to_project(token, slug):
    """Move staged files into projects/<slug>/inputs/ and list them in CONTEXT.md."""
    if not token:
        return []
    files = listing(token)
    if not files:
        return []
    src, dst = _token(token), projects.pdir(slug) / "inputs"
    dst.mkdir(exist_ok=True)
    moved = []
    for f in files:
        name, n = f["name"], 2
        stem, suf = Path(name).stem, Path(name).suffix
        while (dst / name).exists():
            name, n = f"{stem} ({n}){suf}", n + 1
        shutil.move(str(src / f["name"]), str(dst / name))
        moved.append({**f, "name": name})
    shutil.rmtree(src, ignore_errors=True)
    lines = "\n".join(f"- `inputs/{m['name']}` ({m['kind']}, {m['size'] / 1024:.0f} KB)" for m in moved)
    ctx = projects.cache(slug) / "CONTEXT.md"
    with open(ctx, "a", encoding="utf-8") as fh:
        fh.write(f"\n## Input files (from the user, {time.strftime('%Y-%m-%d')})\n"
                 f"The user attached these files to the brief. They are in the project's `inputs/` folder: read the ones "
                 f"relevant to your task before you start (the Read tool handles text, code, PDFs and images).\n{lines}\n")
    projects.log_change(slug, "you", f"attached {len(moved)} file(s): {', '.join(m['name'] for m in moved)}")
    return moved


def cleanup(max_age_h=48):
    if not STAGE.exists():
        return
    cutoff = time.time() - max_age_h * 3600
    for d in STAGE.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
