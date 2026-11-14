"""Start the Agent Studio server on http://127.0.0.1:8777 (set STUDIO_PORT to change it).

  python run.py              start the server and open the studio in your browser
  python run.py --no-browser start the server only (the desktop window uses this)
"""
import os
import site
import sys
import threading
import webbrowser

# Some packages may live in the per-user folder (pip --user). Make sure it is always on the path, even when Windows
# starts us in a way that skips it, so a missing "anyio" (or similar) can't stop the studio from opening.
try:
    _user_site = site.getusersitepackages()
    if os.path.isdir(_user_site) and _user_site not in sys.path:
        site.addsitedir(_user_site)
except Exception:  # noqa: BLE001
    pass

import uvicorn  # noqa: E402

PORT = int(os.environ.get("STUDIO_PORT", "8777"))

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    uvicorn.run("studio.server:app", host="127.0.0.1", port=PORT, log_level="warning")
