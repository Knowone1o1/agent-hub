"""Start the Agent Studio server on http://127.0.0.1:8777 (set STUDIO_PORT to change it).

  python run.py              start the server and open the studio in your browser
  python run.py --no-browser start the server only (the desktop window uses this)
"""
import os
import sys
import threading
import webbrowser

import uvicorn

PORT = int(os.environ.get("STUDIO_PORT", "8777"))

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    uvicorn.run("studio.server:app", host="127.0.0.1", port=PORT, log_level="warning")
