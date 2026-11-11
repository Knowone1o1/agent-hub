"""
Agent Hub desktop app: a native Windows window (WebView2), no browser.

  pythonw desktop\\app.py              -> Agent Hub (chats, projects, Agent Lab)
  pythonw desktop\\app.py --dashboard  -> Team dashboard window
  pythonw desktop\\app.py --lab        -> opens straight into the Agent Lab

It starts the local hub engine (server.py) invisibly if it isn't running yet. When the last window closes the engine is
stopped too, unless a Lab study is running (then it keeps working in the background; reopen the app to watch it).

Needs: Python 3.10+, `pip install pythonnet`, the .NET 8+ Windows Desktop runtime and the WebView2 runtime
(both preinstalled on current Windows 11). The WebView2 libraries are bundled in desktop/lib.
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
PORT = 8765
URL = f"http://127.0.0.1:{PORT}"
NO_WINDOW, DETACHED = 0x08000000, 0x00000008


def message(text, title="Agent Hub"):
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
    (ROOT / "data").mkdir(exist_ok=True)
    log = open(ROOT / "data" / "server.log", "a", encoding="utf-8")
    subprocess.Popen([str(pyw if pyw.exists() else exe), str(ROOT / "server.py")], cwd=str(ROOT), stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, creationflags=NO_WINDOW | DETACHED)
    for _ in range(60):
        if server_up():
            return True
        time.sleep(0.25)
    return False


def study_running():
    try:
        r = json.load(urllib.request.urlopen(URL + "/api/lab/studies", timeout=3))
        return bool(r.get("runner", {}).get("alive"))
    except Exception:
        return False


def stop_server():
    try:
        urllib.request.urlopen(URL + "/api/shutdown", data=b"{}", timeout=2)
    except Exception:
        pass


def load_dotnet():
    """Modern .NET with the Windows Desktop framework (works on ARM64 and x64)."""
    from clr_loader import get_coreclr
    from pythonnet import set_runtime
    set_runtime(get_coreclr(runtime_config=str(HERE / "agenthub.runtimeconfig.json")))
    import clr
    for ref in ("System.Windows.Forms", "System.Drawing", "System.Drawing.Primitives", "System.Drawing.Common"):
        try:
            clr.AddReference(ref)
        except Exception:
            pass
    # WebView2Loader.dll must match the process architecture
    loader_dir = LIB if platform.machine().upper() in ("ARM64", "AARCH64") else LIB / "x64"
    os.environ["PATH"] = f"{loader_dir};{LIB};" + os.environ.get("PATH", "")
    ctypes.windll.kernel32.SetDllDirectoryW(str(loader_dir))
    clr.AddReference(str(LIB / "Microsoft.Web.WebView2.Core.dll"))
    clr.AddReference(str(LIB / "Microsoft.Web.WebView2.WinForms.dll"))
    return clr


def run_ui(path, title, size, icon):
    ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED: WinForms + WebView2 need an STA thread
    # own taskbar identity, so Windows shows the Agent Hub icon instead of grouping it under Python
    app_id = "AgentHub.Dashboard" if "--dashboard" in sys.argv else "AgentHub.App"
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    load_dotnet()
    import System
    import System.Windows.Forms as WinForms
    from System.Drawing import Size, Icon
    from Microsoft.Web.WebView2.WinForms import WebView2, CoreWebView2CreationProperties

    WinForms.Application.EnableVisualStyles()
    try:
        WinForms.Application.SetHighDpiMode(WinForms.HighDpiMode.PerMonitorV2)
    except Exception:
        pass
    form = WinForms.Form()
    form.Text = title
    form.Size = Size(*size)
    form.StartPosition = WinForms.FormStartPosition.CenterScreen
    if icon.exists():
        form.Icon = Icon(str(icon))
    if "--dashboard" in sys.argv:
        form.WindowState = WinForms.FormWindowState.Maximized

    web = WebView2()
    props = CoreWebView2CreationProperties()
    props.UserDataFolder = str(ROOT / "data" / "app-webview")
    web.CreationProperties = props
    web.Dock = WinForms.DockStyle.Fill
    form.Controls.Add(web)

    def on_ready(sender, args):
        if not args.IsSuccess:
            message(f"The app window could not start WebView2:\n{args.InitializationException}")
            return
        core = web.CoreWebView2
        core.Settings.IsStatusBarEnabled = False
        core.Settings.AreDevToolsEnabled = "--devtools" in sys.argv
        # keep the page title in the window title (e.g. "(1) Your turn · Team Dashboard")
        core.DocumentTitleChanged += lambda s, e: setattr(form, "Text", core.DocumentTitle or title)

        def on_new_window(s, e):  # window.open (e.g. the dashboard) -> a new app process, not a browser
            target = str(e.Uri)
            e.Handled = True
            if target.startswith(URL):
                args = ["--dashboard"] if "/dashboard" in target else ["--url", target[len(URL):]]
                pyw = Path(sys.executable).with_name("pythonw.exe")
                subprocess.Popen([str(pyw if pyw.exists() else sys.executable), str(Path(__file__)), *args],
                                 creationflags=DETACHED)
            else:
                os.startfile(target)  # external links still open in the user's browser
        core.NewWindowRequested += on_new_window

    web.CoreWebView2InitializationCompleted += on_ready
    web.Source = System.Uri(URL + path)

    if "--selftest" in sys.argv:  # open, check the page rendered, report to data/app-selftest.json, close
        timer = WinForms.Timer()
        timer.Interval = 6000

        def tick(s, e):
            timer.Stop()
            report = {"ready": web.CoreWebView2 is not None, "title": form.Text}
            def got(task):
                report["page"] = str(task.Result)
                (ROOT / "data" / "app-selftest.json").write_text(json.dumps(report), encoding="utf-8")
                form.BeginInvoke(System.Action(form.Close))
            if web.CoreWebView2 is not None:
                web.CoreWebView2.ExecuteScriptAsync(
                    "JSON.stringify({title: document.title, body: document.body.innerText.slice(0, 80)})").ContinueWith(
                    System.Action[System.Threading.Tasks.Task[System.String]](got))
            else:
                (ROOT / "data" / "app-selftest.json").write_text(json.dumps(report), encoding="utf-8")
                form.Close()
        timer.Tick += tick
        timer.Start()

    WinForms.Application.Run(form)


def main():
    if not server_up() and not start_server():
        message(f"Agent Hub's engine could not start.\nSee {ROOT / 'data' / 'server.log'}")
        return
    if "--dashboard" in sys.argv:
        path, title, size, icon = "/dashboard", "Team Dashboard", (1600, 900), ROOT / "static" / "dashboard.ico"
    else:
        path = "/lab" if "--lab" in sys.argv else "/"
        if "--url" in sys.argv:
            path = sys.argv[sys.argv.index("--url") + 1]
        title, size, icon = "Agent Hub", (1400, 900), ROOT / "static" / "app.ico"
    try:
        run_ui(path, title, size, icon)
    except Exception as e:  # noqa: BLE001
        message(f"Agent Hub couldn't open its window:\n{type(e).__name__}: {e}\n\n"
                "It needs the .NET Desktop runtime and the WebView2 runtime (both standard on Windows 11), "
                "and `pip install pythonnet`.")
        return
    # last window closed: stop the engine unless a study is still working or another app window is open
    others = subprocess.run(["tasklist", "/FI", "IMAGENAME eq pythonw.exe", "/V", "/FO", "CSV"], capture_output=True, text=True).stdout
    if "Agent Hub" not in others and "Team Dashboard" not in others and not study_running():
        stop_server()


if __name__ == "__main__":
    main()
