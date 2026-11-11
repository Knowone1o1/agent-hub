@echo off
rem Opens the Agent Hub desktop app (starts the background engine if needed).
cd /d "%~dp0"
start "" pyw desktop\app.py %*
