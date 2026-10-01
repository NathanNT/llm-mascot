"""Every way of starting a new chat with Claude or ChatGPT that is installed on this PC (no Tk, no network)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import webbrowser
from pathlib import Path

from i18n import tr

HOME = Path.home()
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PRODUCTS = {
    "claude": {"extension": "anthropic.claude-code", "uri": "{scheme}://anthropic.claude-code/open", "cli": "claude",
               "web": "https://claude.ai/new", "app": lambda name, app_id: name == "Claude"},
    "openai": {"extension": "openai.chatgpt", "uri": "{scheme}://openai.chatgpt/", "cli": "codex",
               "web": "https://chatgpt.com/", "app": lambda name, app_id: name in ("ChatGPT", "Codex") and app_id.startswith("OpenAI.")},
}
EDITORS = (("vscode", ".vscode", "In VS Code", "code"), ("cursor", ".cursor", "In Cursor", "cursor"))


def start_menu_apps() -> list[dict]:
    """[{'Name', 'AppID'}] of everything in the Start menu, including Microsoft Store apps."""
    try:
        completed = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=25, creationflags=NO_WINDOW)
        data = json.loads(completed.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    return [data] if isinstance(data, dict) else [item for item in data if isinstance(item, dict)]


def extension_installed(folder: str, extension: str, home: Path = HOME) -> bool:
    root = home / folder / "extensions"
    try:
        return any(entry.name.lower().startswith(extension + "-") for entry in root.iterdir())
    except OSError:
        return False


def editor_exe(command: str, which=shutil.which) -> str | None:
    """Code.exe / Cursor.exe, found from the launcher script (.../bin/code.cmd) that is on the PATH."""
    script = which(command)
    if not script:
        return None
    here = Path(script).resolve().parent
    for folder in (here, here.parent, here.parent.parent):
        if (folder / f"{command}.exe").exists():
            return str(folder / f"{command}.exe")
    return None


def default_browser_exe() -> str | None:
    """The program that opens web links, read from the user's default-browser choice."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as key:
            prog_id = winreg.QueryValueEx(key, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id + r"\shell\open\command") as key:
            command = winreg.QueryValueEx(key, "")[0]
    except OSError:
        return None
    path = command.split('"')[1] if command.startswith('"') else command.split(" ")[0]
    return path if Path(path).exists() else None


def options(kind: str, apps: list[dict] | None = None, home: Path = HOME, which=shutil.which,
            browser=default_browser_exe) -> list[dict]:
    """Options for 'claude' or 'openai': [{'label', 'type': 'shell'|'uri'|'terminal'|'url', 'target', 'badge'}], web always last.
    `badge` is the program that will run it (its icon is drawn on the button); empty for the desktop app, which is the logo itself."""
    product = PRODUCTS.get(kind)
    if product is None:
        return []
    found: list[dict] = []
    for item in apps if apps is not None else start_menu_apps():
        if product["app"](str(item.get("Name", "")), str(item.get("AppID", ""))):
            found.append({"label": tr("Desktop app"), "type": "shell", "target": str(item["AppID"]), "badge": ""})
            break
    for scheme, folder, label, command in EDITORS:
        if extension_installed(folder, product["extension"], home):
            found.append({"label": tr(label), "type": "uri", "target": product["uri"].format(scheme=scheme),
                          "badge": editor_exe(command, which) or ""})
    if which(product["cli"]):
        found.append({"label": tr("In a terminal"), "type": "terminal", "target": product["cli"],
                      "badge": which("wt") or os.environ.get("COMSPEC", "")})
    found.append({"label": tr("On the web"), "type": "url", "target": product["web"], "badge": browser() or ""})
    return found


def launch(option: dict) -> None:
    kind, target = option["type"], option["target"]
    if kind == "shell":
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\{target}"], creationflags=NO_WINDOW)
    elif kind == "uri":
        os.startfile(target)
    elif kind == "terminal":
        subprocess.Popen(["cmd", "/c", "start", "", "cmd", "/k", target], cwd=str(HOME))
    else:
        webbrowser.open(target)
