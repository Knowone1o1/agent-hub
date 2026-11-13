"""
Agent Studio desktop window: a native Windows window (WebView2) around the local studio server. No browser.

  pythonw desktop\\app.py             open the studio (starts the server invisibly if needed)
  pythonw desktop\\app.py --devtools  same, with the WebView2 dev tools enabled

When the window closes, the server stops too, unless an agent or sprint is still working (it then finishes in the
background; reopen the window to watch). Needs `pip install pythonnet`, plus the .NET Desktop runtime and the WebView2
runtime (both standard on Windows 11). The WebView2 libraries are bundled in desktop/lib.
"""
import ctypes
import json
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LIB = HERE / "lib"
PORT = int(os.environ.get("STUDIO_PORT", "8777"))
URL = f"http://127.0.0.1:{PORT}"
NO_WINDOW, DETACHED = 0x08000000, 0x00000008


def message(text, title="Agent Studio"):
    ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)


def server_up():
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
            return True
    except OSError:
        return False


def start_server():
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    (ROOT / "state").mkdir(exist_ok=True)
    log = open(ROOT / "state" / "server.log", "a", encoding="utf-8")
    subprocess.Popen([str(pyw if pyw.exists() else exe), str(ROOT / "run.py"), "--no-browser"], cwd=str(ROOT),
                     stdout=log, stderr=log, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW | DETACHED)
    for _ in range(80):
        if server_up():
            return True
        time.sleep(0.25)
    return False


def anything_working():
    try:
        st = json.load(urllib.request.urlopen(URL + "/api/state", timeout=3))
        return any(a["live"]["state"] in ("working", "thinking") for a in st["agents"]) or \
            any((p.get("sprint") or {}).get("running") for p in st["projects"])
    except Exception:
        return False


def stop_server():
    try:
        urllib.request.urlopen(URL + "/api/shutdown", data=b"{}", timeout=2)
    except Exception:
        pass


def load_dotnet():
    from clr_loader import get_coreclr
    from pythonnet import set_runtime
    set_runtime(get_coreclr(runtime_config=str(HERE / "studio.runtimeconfig.json")))
    import clr
    for ref in ("System.Windows.Forms", "System.Drawing", "System.Drawing.Primitives", "System.Drawing.Common"):
        try:
            clr.AddReference(ref)
        except Exception:
            pass
    loader_dir = LIB if platform.machine().upper() in ("ARM64", "AARCH64") else LIB / "x64"
    os.environ["PATH"] = f"{loader_dir};{LIB};" + os.environ.get("PATH", "")
    ctypes.windll.kernel32.SetDllDirectoryW(str(loader_dir))
    clr.AddReference(str(LIB / "Microsoft.Web.WebView2.Core.dll"))
    clr.AddReference(str(LIB / "Microsoft.Web.WebView2.WinForms.dll"))


def run_ui():
    ctypes.windll.ole32.CoInitializeEx(None, 0x2)   # STA thread for WinForms + WebView2
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AgentStudio.App")
    load_dotnet()
    import System
    import System.Windows.Forms as WinForms
    from System.Drawing import Color, Icon, Size
    from Microsoft.Web.WebView2.WinForms import CoreWebView2CreationProperties, WebView2

    WinForms.Application.EnableVisualStyles()
    try:
        WinForms.Application.SetHighDpiMode(WinForms.HighDpiMode.PerMonitorV2)
    except Exception:
        pass
    form = WinForms.Form()
    form.Text = "Agent Studio"
    form.Size = Size(1560, 960)
    form.BackColor = Color.FromArgb(13, 14, 20)
    form.StartPosition = WinForms.FormStartPosition.CenterScreen
    work = WinForms.Screen.PrimaryScreen.WorkingArea
    if work.Width < 1700 or work.Height < 1000:   # smaller screens: use all of it
        form.WindowState = WinForms.FormWindowState.Maximized
    if (HERE / "studio.ico").exists():
        form.Icon = Icon(str(HERE / "studio.ico"))
    web = WebView2()
    props = CoreWebView2CreationProperties()
    props.UserDataFolder = str(ROOT / "state" / "webview")
    web.CreationProperties = props
    web.DefaultBackgroundColor = Color.FromArgb(13, 14, 20)
    web.Dock = WinForms.DockStyle.Fill
    form.Controls.Add(web)

    def on_ready(sender, args):
        if not args.IsSuccess:
            message(f"The window could not start WebView2:\n{args.InitializationException}")
            return
        core = web.CoreWebView2
        core.Settings.IsStatusBarEnabled = False
        core.Settings.AreDevToolsEnabled = "--devtools" in sys.argv

        def on_new_window(s, e):
            e.Handled = True
            os.startfile(str(e.Uri))   # external links open in the user's browser
        core.NewWindowRequested += on_new_window

    web.CoreWebView2InitializationCompleted += on_ready
    web.Source = System.Uri(URL + "/")

    if "--selftest" in sys.argv:   # open, check the page rendered, write state/app-selftest.json, close
        timer = WinForms.Timer()
        timer.Interval = 7000

        def tick(s, e):
            timer.Stop()
            report = {"ready": web.CoreWebView2 is not None}

            def got(task):
                report["page"] = str(task.Result)
                (ROOT / "state" / "app-selftest.json").write_text(json.dumps(report), encoding="utf-8")
                form.BeginInvoke(System.Action(form.Close))
            if web.CoreWebView2 is not None:
                web.CoreWebView2.ExecuteScriptAsync(
                    "JSON.stringify({title: document.title, cli: document.querySelector('#cliBadge').innerText, "
                    "agents: document.querySelectorAll('[data-aid]').length})").ContinueWith(
                    System.Action[System.Threading.Tasks.Task[System.String]](got))
            else:
                (ROOT / "state" / "app-selftest.json").write_text(json.dumps(report), encoding="utf-8")
                form.Close()
        timer.Tick += tick
        timer.Start()

    WinForms.Application.Run(form)


def main():
    if not server_up() and not start_server():
        message(f"Agent Studio's server could not start.\nSee {ROOT / 'state' / 'server.log'}")
        return
    try:
        run_ui()
    except Exception as e:  # noqa: BLE001
        message(f"Agent Studio couldn't open its window:\n{type(e).__name__}: {e}\n\n"
                f"You can still use it in a browser at {URL}")
        return
    if not anything_working():
        stop_server()


if __name__ == "__main__":
    main()
