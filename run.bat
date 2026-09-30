@echo off
rem Starts LLM Mascot without a console window.
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0rover.py"
