"""Manual check (needs a Windows desktop and the `base` model): drives the benchmark window with a synthetic voice.

    python tests/gui/check_benchmark.py                       # run the check
    python tests/gui/check_benchmark.py --lang en --shot docs/img/en/benchmark.png --models base,turbo
"""
import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

parser = argparse.ArgumentParser()
parser.add_argument("--lang", default="en")
parser.add_argument("--shot", default="")
parser.add_argument("--models", default="base")
args = parser.parse_args()

import settings as prefs

work = Path(tempfile.mkdtemp())
prefs.SETTINGS_FILE = work / "settings.json"
prefs.SETTINGS_FILE.write_text(json.dumps({"ui_language": args.lang, "language": args.lang, "theme": "dark"}))

import benchmark

voice = {"fr": "Microsoft Hortense Desktop", "en": "Microsoft Zira Desktop"}[args.lang]
wav = work / "speech.wav"
script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.SelectVoice('{voice}'); $s.SetOutputToWaveFile('{wav}'); $s.Speak([Console]::In.ReadToEnd()); $s.Dispose()")
subprocess.run(["powershell", "-NoProfile", "-Command", script], input=benchmark.EXAMPLES[args.lang], text=True, encoding="utf-8", check=True)

window = benchmark.Window()
root = window.root
wanted = set(args.models.split(","))


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.01)


window.choose_language(args.lang)
window.set_reference(benchmark.EXAMPLES[args.lang])
window.audio = benchmark.load_wav(wav)
window.recording_label.configure(text=f"Loaded {len(window.audio) / benchmark.SAMPLE_RATE:.1f} s of audio")
for item in window.items:
    window.checks[item["id"]].set(item["id"] in wanted)
window.run()
deadline = time.monotonic() + 180
while window.running and time.monotonic() < deadline:
    pump(0.2)
pump(0.6)

assert not window.running, "the benchmark did not finish"
assert set(window.results) == wanted, window.results.keys()
for name, result in window.results.items():
    assert result["wer"] is not None, (name, result["error"])
    assert 0 <= result["wer"] < 0.8 and result["seconds"] > 0, (name, result["wer"])
rows = [window.table.item(child)["values"] for child in window.table.get_children()]
assert len(rows) == len(wanted) and all("%" in str(row[1]) for row in rows), rows
assert "Best balance" in window.summary.cget("text") or "compromis" in window.summary.cget("text")
window.table.selection_set(window.table.get_children()[0])
pump(0.3)
assert "Transcri" in window.detail.get("1.0", "end")

# the guard rails: nothing to run without a text, a recording or a model
window.set_reference("")
window.run()
assert not window.running and window.status.cget("text")

if args.shot:
    import ctypes
    from ctypes import wintypes
    from PIL import ImageGrab

    window.set_reference(benchmark.EXAMPLES[args.lang])
    window.status.configure(text="")
    pump(0.4)
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(window.rover.layered.toplevel_hwnd(root), ctypes.byref(rect))
    image = ImageGrab.grab(all_screens=True)
    left, top = window.rover.desktop_bounds()[:2]
    Path(args.shot).parent.mkdir(parents=True, exist_ok=True)
    image.crop((rect.left - left, rect.top - top, rect.right - left, rect.bottom - top)).save(args.shot)
print({"result": "PASS", "wer": {n: round(r["wer"], 3) for n, r in window.results.items()},
       "seconds": {n: round(r["seconds"], 2) for n, r in window.results.items()}})
window.close()
