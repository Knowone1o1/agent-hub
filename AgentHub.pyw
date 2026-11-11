"""
Agent Hub launcher (kept for old shortcuts). Opens the native desktop app (desktop/app.py).

  AgentHub.pyw              -> main app window
  AgentHub.pyw --dashboard  -> team dashboard window
  AgentHub.pyw --lab        -> Agent Lab
  AgentHub.pyw --stop       -> stop the background engine
"""
import runpy
import sys
import urllib.request
from pathlib import Path

if "--stop" in sys.argv:
    try:
        urllib.request.urlopen("http://127.0.0.1:8765/api/shutdown", data=b"{}", timeout=2)
    except Exception:
        pass
else:
    runpy.run_path(str(Path(__file__).resolve().parent / "desktop" / "app.py"), run_name="__main__")
