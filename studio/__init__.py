"""Agent Studio: a living multi-agent workspace that runs every agent through the local `claude` CLI."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / ".agents"
PROJECTS_DIR = ROOT / "projects"
TEMPLATES_DIR = ROOT / "templates"
STATE_DIR = ROOT / "state"
PUBLIC_DIR = ROOT / "public"
for d in (AGENTS_DIR, PROJECTS_DIR, STATE_DIR):
    d.mkdir(exist_ok=True)
