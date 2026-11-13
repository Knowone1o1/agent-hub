@echo off
rem Agent Studio: opens in its own window (WebView2). Falls back to the browser if pythonnet is missing.
cd /d "%~dp0"
py -c "import clr_loader, pythonnet" 2>nul && (start "" pyw desktop\app.py) || (py run.py)
