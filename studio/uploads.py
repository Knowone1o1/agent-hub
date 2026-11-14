"""Files the user adds to a project.

Uploads are streamed straight to disk in chunks (no size limit beyond free disk space). Files dropped into a new
project's brief are staged under state/uploads/<token>/ and shown to the Principal (text files as excerpts, everything
else by name and size); when the team is assembled they move to projects/<slug>/inputs/. Files added to a running
project go straight into its inputs/ folder. Either way they are listed in CONTEXT.md, so every agent sees them on its
next turn and can open them with the Read tool (or process large data with scripts if terminal commands are enabled).
"""
import re
import shutil
import time
import uuid
from pathlib import Path

from . import STATE_DIR, projects

STAGE = STATE_DIR / "uploads"
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".xml", ".html", ".htm",
            ".css", ".js", ".ts", ".tsx", ".jsx", ".py", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".rb", ".php", ".sql",
            ".sh", ".bat", ".ps1", ".ini", ".toml", ".cfg", ".log", ".rst", ".tex", ".svg", ".kt", ".swift", ".r", ".m", ".ipynb"}
DATA_EXT = {".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".parquet", ".xlsx", ".xls", ".sqlite", ".db", ".feather", ".h5",
            ".hdf5", ".npy", ".npz", ".pkl", ".zip", ".gz", ".tar", ".7z", ".avro", ".orc", ".sav", ".dta"}


def safe_name(name):
    name = Path(str(name).replace("\\", "/")).name.strip() or "file"
    name = re.sub(r"[^\w.\- ()\[\]]+", "_", name)[:140]
    return name.lstrip(".") or "file"


def _token_dir(token):
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        raise ValueError("Bad upload token.")
    return STAGE / token


def kind_of(path):
    ext = Path(path).suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext in DATA_EXT:
        return "data"
    if ext in TEXT_EXT:
        return "text"
    return "file"


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def _unique(folder, name):
    name, n = safe_name(name), 2
    stem, suf = Path(name).stem, Path(name).suffix
    while (folder / name).exists() or (folder / (name + ".part")).exists():
        name, n = f"{stem} ({n}){suf}", n + 1
    return folder / name


def check_space(folder, size):
    folder.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(folder).free
    if size and size > free - 512 * 1024 * 1024:   # keep 512 MB spare
        raise ValueError(f"Not enough disk space: the file is {human(size)}, {human(free)} free.")


def listing_dir(folder):
    if not folder.exists():
        return []
    return [{"name": p.name, "size": p.stat().st_size, "kind": kind_of(p)} for p in sorted(folder.iterdir())
            if p.is_file() and not p.name.endswith(".part")]


def listing(token):
    return listing_dir(_token_dir(token))


# ---------------------------------------------------------------- streaming writes

def stage_target(token, name, size=0):
    """Where a streamed brief attachment goes. Returns (token, final path)."""
    token = token or uuid.uuid4().hex
    d = _token_dir(token)
    check_space(d, size)
    return token, _unique(d, name)


def project_target(slug, name, size=0):
    d = projects.pdir(slug) / "inputs"
    check_space(d, size)
    return _unique(d, name)


def remove(token, name):
    p = _token_dir(token) / safe_name(name)
    if p.exists():
        p.unlink()
    return listing(token)


# ---------------------------------------------------------------- what the Principal and the agents see

def summary_for_principal(token, budget=24000):
    if not token:
        return ""
    files = listing(token)
    if not files:
        return ""
    d, out, left = _token_dir(token), [], budget
    for f in files:
        line = f"- inputs/{f['name']} ({f['kind']}, {human(f['size'])})"
        if f["kind"] in ("text", "data") and left > 500 and Path(f["name"]).suffix.lower() in TEXT_EXT:
            try:
                with open(d / f["name"], encoding="utf-8", errors="replace") as fh:
                    excerpt = fh.read(min(left, 6000))   # only the start: big files are never loaded whole
            except OSError:
                excerpt = ""
            left -= len(excerpt)
            line += f"\n  ```\n{excerpt}\n  ```" + ("\n  (start of the file only)" if f["size"] > len(excerpt.encode()) else "")
        out.append(line)
    return "\n".join(out)


def note_in_context(slug, files):
    """List new input files in CONTEXT.md under one "## Input files" section (created on first use)."""
    if not files:
        return
    big = any(f["size"] > 20 * 1024 * 1024 or f["kind"] == "data" for f in files)
    lines = "".join(f"- `inputs/{f['name']}` ({f['kind']}, {human(f['size'])}, added {time.strftime('%Y-%m-%d %H:%M')})\n" for f in files)
    ctx_path = projects.cache(slug) / "CONTEXT.md"
    ctx = ctx_path.read_text(encoding="utf-8")
    if big and "never try to load a huge file whole" not in ctx:
        lines = ("- Note for large data files: read only the first lines to learn the format, then work with scripts if "
                 "terminal commands are enabled; never try to load a huge file whole.\n") + lines
    head = "## Input files"
    if head not in ctx:
        ctx = ctx.rstrip() + (f"\n\n{head}\nFiles from the user, in the project's `inputs/` folder. Read the ones relevant to "
                              "your task before you start (the Read tool handles text, code, PDFs and images). For large "
                              "data files, read only the first lines to learn the format, then work with scripts if "
                              "terminal commands are enabled; never try to load a huge file whole.\n")
        ctx += lines
    else:
        start = ctx.index(head)
        nxt = ctx.find("\n## ", start + len(head))
        ctx = ctx[:nxt].rstrip() + "\n" + lines + ctx[nxt:] if nxt >= 0 else ctx.rstrip() + "\n" + lines
    ctx_path.write_text(ctx, encoding="utf-8")
    names = ", ".join(f"{f['name']} ({human(f['size'])})" for f in files)
    projects.log_change(slug, "you", f"added input file(s): {names}" + (" · large data: agents should sample it, not read it whole" if big else ""))


def attach_staged(token, slug):
    """New project assembled: move the staged brief attachments into inputs/."""
    if not token:
        return []
    files = listing(token)
    if not files:
        return []
    src, moved = _token_dir(token), []
    for f in files:
        dst = project_target(slug, f["name"])
        shutil.move(str(src / f["name"]), str(dst))   # same drive: an instant rename, even for huge files
        moved.append({**f, "name": dst.name})
    shutil.rmtree(src, ignore_errors=True)
    note_in_context(slug, moved)
    return moved


def project_files(slug):
    return listing_dir(projects.pdir(slug) / "inputs")


def cleanup(max_age_h=48):
    if not STAGE.exists():
        return
    cutoff = time.time() - max_age_h * 3600
    for d in STAGE.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
