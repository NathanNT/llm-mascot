"""Developer shortcuts: restart the app, run the tests and the desktop checks, regenerate the screenshots, mimic the CI.

    .venv\\Scripts\\python tools\\dev.py restart           stop the running mascot and start it again with the current code
    .venv\\Scripts\\python tools\\dev.py test              unit tests (no display needed)
    .venv\\Scripts\\python tools\\dev.py check [names]     the desktop checks of tests\\gui (all the safe ones, or the names given)
    .venv\\Scripts\\python tools\\dev.py shots             regenerate every picture of the README (dark, light, Windows XP; EN and FR)
    .venv\\Scripts\\python tools\\dev.py ci                run the unit tests in a clean environment with only the CI's packages
    .venv\\Scripts\\python tools\\dev.py all               test + check + ci, then restart

The desktop checks move the real pointer and register real shortcuts, so the running mascot is stopped first and restarted at the end
(--no-restart to leave it stopped). `check_insert` and `check_focus` type into whatever window is in front, so they only run when named.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)
PYTHONW = PYTHON.with_name("pythonw.exe") if PYTHON.with_name("pythonw.exe").exists() else PYTHON
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}
NOISE = ("RuntimeError: main thread is not in main loop", "Exception in thread")
UNSAFE = {"check_insert", "check_focus", "check_animation"}      # they type into, or steal focus from, the window in front
SHOT_SETS = (("en", "dark", "en"), ("fr", "dark", "fr"), ("en", "light", "en-light"), ("en", "xp", "en-xp"), ("fr", "xp", "fr-xp"))
KEEP_FOR_XP = {"hero.png", "settings.png", "advanced.png", "states.png", "editor.png"}


def run(command: list[str], timeout: int = 600, **extra) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=ROOT, env={**ENV, **extra.pop("env", {})}, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, **extra)


def stop_app() -> int:
    """Stop every running copy of the mascot (and what it started); returns how many were running."""
    script = ("$p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'rover\\.py' -and $_.Name -match 'python' }; "
              "$p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; ($p | Measure-Object).Count")
    result = run(["powershell", "-NoProfile", "-Command", script], timeout=30)
    time.sleep(0.8)
    try:
        return int(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def start_app() -> None:
    subprocess.Popen([str(PYTHONW), "rover.py"], cwd=ROOT, creationflags=0x00000008 | 0x00000200)      # detached: it outlives this script


def restart(_args=None) -> int:
    stopped = stop_app()
    start_app()
    print(f"mascot restarted ({stopped} stopped)")
    return 0


def test(_args=None) -> int:
    started = time.time()
    result = run([str(PYTHON), "-m", "pytest", "-q", "tests/unit"])
    print(result.stdout.strip().splitlines()[-1] if result.stdout.strip() else result.stderr.strip()[-300:], f"({time.time() - started:.0f} s)")
    if result.returncode:
        print(result.stdout[-2500:])
    return result.returncode


def check(args) -> int:
    names = [f"check_{name.removeprefix('check_')}" for name in args.names]
    available = sorted(path.stem for path in (ROOT / "tests" / "gui").glob("check_*.py"))
    chosen = names or [name for name in available if name not in UNSAFE]
    unknown = [name for name in chosen if name not in available]
    if unknown:
        print("unknown check:", ", ".join(unknown), "| available:", ", ".join(available))
        return 2
    was_running = stop_app()
    failures = 0
    for name in chosen:
        started = time.time()
        try:
            result = run([str(PYTHON), f"tests/gui/{name}.py"], timeout=240)
            output = result.stdout + result.stderr
            passed = "PASS" in output
        except subprocess.TimeoutExpired:
            output, passed = "timed out", False
        print(f"{'PASS' if passed else 'FAIL':5} {name:22} {time.time() - started:5.1f} s")
        if not passed:
            failures += 1
            lines = [line for line in output.splitlines() if line.strip() and not any(noise in line for noise in NOISE)]
            print("      " + "\n      ".join(lines[-6:]))
    if was_running and not args.no_restart:
        start_app()
    print(f"{len(chosen) - failures}/{len(chosen)} checks passed")
    return 1 if failures else 0


def shots(args) -> int:
    stop_app()
    for language, theme, folder in SHOT_SETS:
        out = ROOT / "docs" / "img" / folder
        result = run([str(PYTHON), "tools/make_screenshots.py", "--lang", language, "--theme", theme, "--out", str(out)], timeout=300)
        print(f"{folder:8} {'ok' if result.returncode == 0 else 'FAILED'}")
        if folder.endswith("-xp"):
            for picture in out.glob("*.png"):
                if picture.name not in KEEP_FOR_XP:
                    picture.unlink()
            env = {"ROVER_TEST_THEME": "xp", "ROVER_TEST_LANG": language, "ROVER_TEST_SHOT": str(out / "editor.png")}
            run([str(PYTHON), "tests/gui/check_editor.py"], timeout=240, env=env)
    if not args.no_restart:
        start_app()
    return 0


def ci(_args=None) -> int:
    folder = Path(tempfile.gettempdir()) / "llm-mascot-ci"
    if not (folder / "Scripts" / "python.exe").exists():
        subprocess.run([str(PYTHON), "-m", "venv", str(folder)], check=True)
        subprocess.run([str(folder / "Scripts" / "python.exe"), "-m", "pip", "install", "-q", "pillow", "numpy", "requests", "pytest"], check=True)
    python = folder / "Scripts" / "python.exe"
    compiled = run([str(python), "-m", "compileall", "-q", ".", "-x", "design|.venv"])
    result = run([str(python), "-m", "pytest", "-q", "tests"])
    print("compileall:", "ok" if compiled.returncode == 0 else "FAILED")
    print("pytest (CI packages only):", result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "no output")
    return compiled.returncode or result.returncode


def everything(args) -> int:
    code = test() or ci()
    args.names = []
    args.no_restart = True
    code = check(args) or code
    start_app()
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("restart").set_defaults(run=restart)
    sub.add_parser("test").set_defaults(run=test)
    sub.add_parser("ci").set_defaults(run=ci)
    checker = sub.add_parser("check")
    checker.add_argument("names", nargs="*", help="check names, with or without the check_ prefix")
    checker.add_argument("--no-restart", action="store_true")
    checker.set_defaults(run=check)
    picture = sub.add_parser("shots")
    picture.add_argument("--no-restart", action="store_true")
    picture.set_defaults(run=shots)
    sub.add_parser("all").set_defaults(run=everything, names=[], no_restart=False)
    args = parser.parse_args()
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
