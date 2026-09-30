"""Start LLM Mascot when Windows starts: a per-user Run entry (no admin rights, no shortcut file)."""

from __future__ import annotations

import subprocess
import sys
import winreg
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "LLMMascot"
SCRIPT = HERE / "rover.py"


def launch_command() -> str:
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    return f'"{pythonw if pythonw.exists() else python}" "{SCRIPT}"'


def _powershell(script: str) -> str:
    completed = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=20,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return completed.stdout


def startup_shortcuts() -> list[Path]:
    """Shortcuts in the Startup folder that launch this very rover.py (for example the one install.ps1 -Startup makes)."""
    script = (
        "$s = New-Object -ComObject WScript.Shell; "
        "Get-ChildItem ([Environment]::GetFolderPath('Startup')) -Filter *.lnk | ForEach-Object { "
        "$l = $s.CreateShortcut($_.FullName); if ($l.Arguments -like '*rover.py*') { $_.FullName + '|' + $l.Arguments } }"
    )
    found = []
    try:
        for line in _powershell(script).splitlines():
            path, _, arguments = line.partition("|")
            if path and str(SCRIPT).lower() in arguments.lower():
                found.append(Path(path))
    except (OSError, subprocess.SubprocessError):
        pass
    return found


def _registry_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except OSError:
        return False


def is_enabled() -> bool:
    return _registry_enabled() or bool(startup_shortcuts())


def set_enabled(enabled: bool) -> None:
    if enabled:
        if not is_enabled():
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, launch_command())
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except OSError:
        pass
    for shortcut in startup_shortcuts():
        try:
            shortcut.unlink()
        except OSError:
            pass
