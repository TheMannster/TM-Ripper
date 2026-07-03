@echo off
REM Launcher for Social Video Downloader (no console window)
cd /d "%~dp0"

REM Use pythonw.exe so no black CMD window shows behind the GUI.
start "" pythonw app.py

REM If pythonw isn't found, the GUI won't appear. In that case run: py app.py
