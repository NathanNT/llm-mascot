"""Manual check (needs a Windows desktop): drives the advanced settings page like a user and checks what gets saved."""
import sys
import tempfile
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import settings as prefs

work = Path(tempfile.mkdtemp())
prefs.SETTINGS_FILE = work / "settings.json"
prefs.MASCOT_DIR = work / "mascots"

import rover
import secrets_store
import startup
import styles
import transcribe

rover.read_quotas = lambda *a: []
startup_calls = []
startup.set_enabled = lambda flag: startup_calls.append(flag)
startup.is_enabled = lambda: True

app = rover.RoverApp()
app.handle_hover = lambda now: None
app.update_pref(ui_language="en")          # the labels below are the English ones
app.root.update()
app.open_settings()
window = app.settings_window
window.toggle_page()
window.top.update()


def widgets(root=None):
    root = root or window.top
    for child in root.winfo_children():
        yield child
        yield from widgets(child)


def button(text):
    return next(w for w in widgets() if isinstance(w, tk.Button) and w.cget("text") == text)


def label_click(text):
    widget = next(w for w in widgets() if isinstance(w, tk.Label) and w.cget("text") == text)
    widget.event_generate("<Button-1>")
    window.top.update()


def text_box():
    return next(w for w in widgets() if isinstance(w, tk.Text))


def click(text):
    button(text).invoke()
    window.top.update()


# style presets fill the instruction box; editing it switches to Custom
click("Proofread")
assert app.prefs["rewrite_style"] == "proofread"
assert text_box().get("1.0", "end-1c") == styles.instruction("proofread")
text_box().focus_force()
window.top.update()
text_box().insert("end", " Keep emojis.")
text_box().event_generate("<KeyRelease>", keysym="s")
window.top.update()
assert app.prefs["rewrite_style"] == "custom" and app.prefs["rewrite_prompt"].endswith("Keep emojis.")
click("Short and simple")
assert app.prefs["rewrite_style"] == "concise"
click("Custom")
assert app.prefs["rewrite_style"] == "custom" and app.prefs["rewrite_prompt"].endswith("Keep emojis.")

# speech model, provider, models, startup
label_click("small")
assert app.prefs["whisper_model"] == "small" and app.recorder.model_name == "small"
click("Claude")
assert app.prefs["rewrite_provider"] == "claude"
click("High")
assert app.prefs["codex"]["reasoning"] == "high"
click("opus")
assert app.prefs["claude"]["model"] == "opus"
click("Copy only")
assert app.prefs["insert"] == "copy"
click("Off")
assert app.prefs["startup"] is False

# an external transcription service: pick it, save a key (stored encrypted), choose a fast model, test the connection
import time
transcribe.test_connection = lambda config: (True, "Connected")
click("OpenAI")
for _ in range(5):
    window.top.update()
    time.sleep(0.03)
assert app.prefs["transcription"]["engine"] == "openai"
entry = next(w for w in widgets() if isinstance(w, tk.Entry) and w.cget("show") == "•")
entry.insert(0, "sk-test-key-123")
click("Save key")
stored = app.prefs["transcription"]["api_key"]
assert stored.startswith("dpapi:") and "sk-test" not in stored and secrets_store.unprotect(stored) == "sk-test-key-123"
assert entry.get() == "", "the typed key must be wiped from the field once saved"
click("whisper-1")
assert app.prefs["transcription"]["model"] == "whisper-1"
click("gpt-4o-mini-transcribe")
assert app.prefs["transcription"]["model"] == "", "the default model is stored as empty"
import threading


class InlineThread:                      # this script has no Tk main loop, so run the "background" check right away
    def __init__(self, target=None, args=(), daemon=None, **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


real_thread = threading.Thread
threading.Thread = InlineThread
click("Test")
threading.Thread = real_thread
window.top.update()
assert any(isinstance(w, tk.Label) and "Connected" in w.cget("text") for w in widgets())

# nothing is written until Save; closing reverts
assert not prefs.SETTINGS_FILE.exists()
window.save()
saved = prefs.load()
assert saved["whisper_model"] == "small" and saved["rewrite_provider"] == "claude" and saved["claude"]["model"] == "opus"
assert saved["rewrite_style"] == "custom" and saved["insert"] == "copy" and saved["startup"] is False
assert saved["transcription"]["engine"] == "openai" and "sk-test" not in prefs.SETTINGS_FILE.read_text(encoding="utf-8")
assert startup_calls == [False], startup_calls

# reopen, change something, close without saving: the change is undone, including the speech model
app.open_settings()
window = app.settings_window
window.toggle_page()
window.top.update()
click("This PC")
for _ in range(5):
    window.top.update()
    time.sleep(0.03)
label_click("large-v3")
assert app.recorder.model_name == "large-v3"
window.close()
assert app.prefs["whisper_model"] == "small" and app.recorder.model_name == "small"
assert app.prefs["transcription"]["engine"] == "openai", "closing without saving restores the saved engine"
print({"result": "PASS", "saved": {k: saved[k] for k in ("whisper_model", "rewrite_provider", "rewrite_style", "startup")}})
app.quit()
