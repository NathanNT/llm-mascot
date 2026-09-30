"""Speed and accuracy benchmark for the speech models, on your own words.

Read a text aloud once; every selected model transcribes that same recording and is scored against the text.

    python benchmark.py                                   # the window
    python benchmark.py --audio talk.wav --text ref.txt --models base,turbo,groq --lang fr     # no window
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
import tkinter as tk
import wave
from pathlib import Path
from tkinter import filedialog, ttk

import numpy as np

import scoring
import settings as prefs
import transcribe
import i18n
from i18n import tr

HERE = Path(__file__).resolve().parent
SAMPLE_RATE = 16000
EXAMPLES = {
    "fr": "Ajoute une route POST sur /api/v2/webhooks dans le contrôleur Express, valide le payload avec Zod, "
          "puis pousse l'événement dans la file Redis Streams avec un retry exponentiel et un timeout de trente secondes.",
    "en": "Add a POST route at /api/v2/webhooks in the Express controller, validate the payload with Zod, "
          "then push the event to the Redis Streams queue with exponential retry and a thirty second timeout.",
}


# ---------------------------------------------------------------------------------------------------- audio and runners

def load_wav(path: str | Path) -> np.ndarray:
    """Any PCM wav file as 16 kHz mono float32."""
    with wave.open(str(path), "rb") as handle:
        channels, width, rate, frames = handle.getnchannels(), handle.getsampwidth(), handle.getframerate(), handle.readframes(handle.getnframes())
    if width not in (1, 2, 4):
        raise ValueError(tr("Unsupported WAV format"))
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
    audio = np.frombuffer(frames, dtype=dtype).astype(np.float32)
    audio = (audio - 128) / 128 if width == 1 else audio / float(np.iinfo(dtype).max)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        target = int(len(audio) * SAMPLE_RATE / rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, target), np.arange(len(audio)), audio).astype(np.float32)
    return audio


_models: dict[str, object] = {}


def run_local(name: str, audio: np.ndarray, language: str) -> dict:
    from faster_whisper import WhisperModel
    from voice import MODEL_DIR, local_transcribe

    started = time.perf_counter()
    if name not in _models:
        _models[name] = WhisperModel(name, device="cpu", compute_type="int8", download_root=str(MODEL_DIR))
    load = time.perf_counter() - started
    local_transcribe(_models[name], np.zeros(8000, dtype=np.float32), language)      # warm-up, not timed
    started = time.perf_counter()
    text = local_transcribe(_models[name], audio, language)
    return {"text": text, "seconds": time.perf_counter() - started, "load": load}


def run_remote(config: dict, audio: np.ndarray, language: str) -> dict:
    started = time.perf_counter()
    text = transcribe.transcribe_remote(audio, language, config)
    return {"text": text, "seconds": time.perf_counter() - started, "load": 0.0}


def candidates(settings: dict) -> list[dict]:
    """Every model that can be benchmarked, with whether it is ready to run right now."""
    from voice import model_downloaded

    items = []
    for name, size, _hint in prefs.WHISPER_MODELS:
        ready = model_downloaded(name)
        items.append({"id": name, "label": name, "kind": "local", "ready": ready,
                      "note": tr("installed") if ready else tr("to download {size}").format(size=size)})
    saved = settings["transcription"]
    for engine, preset in transcribe.ENGINES.items():
        if engine == "custom" and saved.get("engine") != "custom":
            continue
        own = saved.get("engine") == engine            # the stored key belongs to the engine it was saved for
        config = {"engine": engine, "model": saved.get("model", "") if own else "", "api_key": saved.get("api_key", "") if own else "",
                  "base_url": saved.get("base_url", "") if own else ""}
        resolved = transcribe.resolve(config)
        ready = bool(resolved["key"] and resolved["base_url"] and resolved["model"])
        items.append({"id": engine, "label": f"{preset['label']} · {resolved['model'] or '…'}", "kind": "remote", "ready": ready,
                      "config": config, "note": tr("audio is sent") if ready else tr("no API key")})
    return items


def benchmark(items: list[dict], audio: np.ndarray, language: str, reference: str, strip_accents: bool, emit) -> list[dict]:
    """Run the items (local ones one after the other, services in parallel); `emit(result)` fires as each finishes."""
    results, lock = [], threading.Lock()

    def one(item):
        try:
            outcome = run_local(item["id"], audio, language) if item["kind"] == "local" else run_remote(item["config"], audio, language)
            graded = scoring.score(reference, outcome["text"], strip_accents)
            result = {"name": item["label"], "id": item["id"], **outcome, "wer": graded.wer, "score": graded, "error": ""}
        except Exception as exc:                   # a missing download, a rejected key… must not stop the others
            result = {"name": item["label"], "id": item["id"], "text": "", "seconds": None, "load": 0.0, "wer": None, "score": None,
                      "error": str(exc)[:160]}
        with lock:
            results.append(result)
        emit(result)

    local = [item for item in items if item["kind"] == "local"]
    threads = [threading.Thread(target=one, args=(item,), daemon=True) for item in items if item["kind"] == "remote"]
    for thread in threads:
        thread.start()
    for item in local:
        one(item)
    for thread in threads:
        thread.join()
    return results


def markdown(results: list[dict], seconds: float) -> str:
    rows = ["| Model | WER | Time | x realtime | Errors (sub/del/ins) |", "|---|---|---|---|---|"]
    for r in sorted(results, key=lambda r: (r["wer"] is None, r["wer"] or 0)):
        if r["wer"] is None:
            rows.append(f"| {r['name']} | failed | | | {r['error']} |")
            continue
        s = r["score"]
        rows.append(f"| {r['name']} | {r['wer'] * 100:.1f} % | {r['seconds']:.2f} s | {seconds / r['seconds']:.1f}x | "
                    f"{s.substitutions}/{s.deletions}/{s.insertions} |")
    return "\n".join(rows)


# ---------------------------------------------------------------------------------------------------- command line

def cli(args) -> int:
    settings = prefs.load()
    audio = load_wav(args.audio)
    reference = Path(args.text).read_text(encoding="utf-8")
    wanted = [m.strip() for m in args.models.split(",")] if args.models else []
    items = [item for item in candidates(settings) if (item["id"] in wanted if wanted else item["ready"])]
    if not items:
        print("Nothing to run: no model selected or ready.")
        return 1
    seconds = len(audio) / SAMPLE_RATE
    done = benchmark(items, audio, args.lang, reference, args.strip_accents,
                     lambda r: print(f"  {r['name']}: " + (f"{r['wer'] * 100:.1f} % in {r['seconds']:.2f} s" if r["wer"] is not None else r["error"])))
    print()
    print(markdown(done, seconds))
    print(scoring.recommend([{"name": r["name"], "wer": r["wer"], "seconds": r["seconds"]} for r in done]))
    return 0


# ---------------------------------------------------------------------------------------------------- window

class Window:
    def __init__(self):
        import rover                                   # the app's own title bar, cards and colours
        self.rover = rover
        self.settings = prefs.load()
        i18n.set_language(self.settings["ui_language"])
        self.theme = rover.THEMES[self.settings["theme"]]
        self.language = self.settings["language"]
        self.audio: np.ndarray | None = None
        self.recording = False
        self.chunks: list[np.ndarray] = []
        self.stream = None
        self.level = 0.0
        self.started = 0.0
        self.results: dict[str, dict] = {}
        self.inbox: queue.Queue = queue.Queue()
        self.running = False

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.build()
        self.root.update_idletasks()
        width, height = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        left, top, right, bottom = rover.monitor_work_area(0, 0)
        rover.place_window(self.root, left + (right - left - width) // 2, top + max(0, (bottom - top - height) // 2), width, height)
        self.root.deiconify()
        self.root.focus_force()
        self.root.after(100, self.poll)

    # -- construction
    def build(self):
        rover, theme = self.rover, self.theme
        shim = type("Shim", (), {"theme": theme})()
        body, _ = rover.make_chrome(self.root, shim, tr("Speech model benchmark"), self.close)
        outer = tk.Frame(body, bg=theme["desk"], padx=24, pady=8)
        outer.pack()
        tk.Label(outer, text=tr("Benchmark"), bg=theme["desk"], fg=theme["text"], font=(rover.FONT_SEMIBOLD, 18)).pack(anchor="w")
        tk.Label(outer, text=tr("Read your own text once; every model transcribes the same recording and is scored against it."),
                 bg=theme["desk"], fg=theme["sec"], font=(rover.FONT_REGULAR, 9)).pack(anchor="w", pady=(0, 10))
        columns = tk.Frame(outer, bg=theme["desk"])
        columns.pack()
        left = tk.Frame(columns, bg=theme["desk"])
        left.pack(side="left", anchor="n", padx=(0, 14))
        right = tk.Frame(columns, bg=theme["desk"])
        right.pack(side="left", anchor="n")
        rover.card(left, theme, 440, self.fill_text, pad=14, outer=theme["desk"]).pack(pady=(0, 10))
        rover.card(left, theme, 440, self.fill_record, pad=14, outer=theme["desk"]).pack(pady=(0, 10))
        rover.card(left, theme, 440, self.fill_models, pad=14, outer=theme["desk"]).pack()
        rover.card(right, theme, 600, self.fill_results, pad=14, outer=theme["desk"]).pack()

    def heading(self, inner, number: str, text: str):
        theme = self.theme
        tk.Label(inner, text=f"{number}  {text}", bg=theme["card"], fg=theme["text"], font=(self.rover.FONT_SEMIBOLD, 10)).pack(anchor="w")

    def entry_box(self, inner, height: int):
        theme = self.theme
        box = tk.Text(inner, width=50, height=height, wrap="word", relief="flat", bg=theme["panel"], fg=theme["text"],
                      insertbackground=theme["text"], highlightthickness=1, highlightbackground=theme["border"],
                      highlightcolor=theme["accent"], font=(self.rover.FONT_REGULAR, 10), padx=8, pady=6)
        box.bind("<Button-1>", lambda event: box.focus_force())
        return box

    def small_button(self, parent, text, command, primary=False):
        return self.rover.button(parent, self.theme, text, command, primary=primary)

    def fill_text(self, inner):
        theme = self.theme
        self.heading(inner, "1", tr("The text you will read"))
        self.reference = self.entry_box(inner, 5)
        self.reference.pack(fill="x", pady=(8, 6))
        row = tk.Frame(inner, bg=theme["card"])
        row.pack(fill="x")
        self.small_button(row, tr("Example"), lambda: self.set_reference(EXAMPLES[self.language])).pack(side="left")
        self.small_button(row, tr("Load text…"), self.load_text).pack(side="left", padx=(6, 0))
        self.language_buttons = {}
        for caption, value in reversed((("Français", "fr"), ("English", "en"))):
            widget = tk.Button(row, text=caption, relief="flat", bd=0, highlightthickness=1, font=(self.rover.FONT_SEMIBOLD, 9),
                               padx=10, pady=4, cursor="hand2", command=lambda v=value: self.choose_language(v))
            widget.pack(side="right", padx=(6, 0))
            self.language_buttons[value] = widget
        self.strip = tk.BooleanVar(value=False)
        tk.Checkbutton(inner, text=tr("Ignore accents when scoring"), variable=self.strip, bg=theme["card"], fg=theme["sec"],
                       selectcolor=theme["panel"], activebackground=theme["card"], activeforeground=theme["text"], bd=0,
                       highlightthickness=0, font=(self.rover.FONT_REGULAR, 9)).pack(anchor="w", pady=(6, 0))
        self.choose_language(self.language)

    def choose_language(self, value: str):
        self.language = value
        for key, widget in self.language_buttons.items():
            self.rover.restyle_toggle(widget, self.theme, key == value, self.theme["card"])

    def fill_record(self, inner):
        theme = self.theme
        self.heading(inner, "2", tr("Your recording"))
        row = tk.Frame(inner, bg=theme["card"])
        row.pack(fill="x", pady=(8, 0))
        self.record_button = tk.Button(row, text="●  " + tr("Record"), command=self.toggle_record, relief="flat", bd=0, padx=16, pady=8,
                                       font=(self.rover.FONT_SEMIBOLD, 10), cursor="hand2", bg=theme["accent"], fg=theme["on_accent"],
                                       activebackground=theme["accent"], activeforeground=theme["on_accent"])
        self.record_button.pack(side="left")
        self.small_button(row, tr("Load audio…"), self.load_audio).pack(side="left", padx=(8, 0))
        self.meter = tk.Canvas(row, width=90, height=10, bg=theme["card"], bd=0, highlightthickness=0)
        self.meter.pack(side="right")
        self.recording_label = tk.Label(inner, text=tr("No recording yet"), bg=theme["card"], fg=theme["sec"], font=(self.rover.FONT_REGULAR, 9))
        self.recording_label.pack(anchor="w", pady=(6, 0))

    def fill_models(self, inner):
        theme = self.theme
        self.heading(inner, "3", tr("Models to compare"))
        self.items = candidates(self.settings)
        self.checks = {}
        for item in self.items:
            var = tk.BooleanVar(value=item["ready"])
            self.checks[item["id"]] = var
            row = tk.Frame(inner, bg=theme["card"])
            row.pack(fill="x", pady=(4, 0))
            check = tk.Checkbutton(row, text=item["label"], variable=var, bg=theme["card"], fg=theme["text"], selectcolor=theme["panel"],
                                   activebackground=theme["card"], activeforeground=theme["text"], bd=0, highlightthickness=0,
                                   font=(self.rover.FONT_REGULAR, 10), anchor="w")
            check.pack(side="left")
            tk.Label(row, text=item["note"], bg=theme["card"], fg=theme["ok"] if item["ready"] else theme["sec"],
                     font=(self.rover.FONT_REGULAR, 8)).pack(side="right")
        actions = tk.Frame(inner, bg=theme["card"])
        actions.pack(fill="x", pady=(10, 0))
        self.run_button = self.small_button(actions, tr("Run the benchmark"), self.run, primary=True)
        self.run_button.pack(side="left")
        self.small_button(actions, tr("Installed only"), lambda: [self.checks[i["id"]].set(i["ready"] and i["kind"] == "local") for i in self.items]
                          ).pack(side="left", padx=(8, 0))
        self.status = tk.Label(inner, text="", bg=theme["card"], fg=theme["sec"], font=(self.rover.FONT_REGULAR, 9), anchor="w",
                               justify="left", wraplength=400)
        self.status.pack(fill="x", pady=(8, 0))

    def fill_results(self, inner):
        theme, rover = self.theme, self.rover
        self.heading(inner, "4", tr("Results"))
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Bench.Treeview", background=theme["panel"], fieldbackground=theme["panel"], foreground=theme["text"], borderwidth=0,
                        rowheight=26, font=(rover.FONT_REGULAR, 10))
        style.configure("Bench.Treeview", bordercolor=theme["panel"], lightcolor=theme["panel"], darkcolor=theme["panel"])
        style.map("Bench.Treeview", background=[("selected", theme["accent"])], foreground=[("selected", theme["on_accent"])])
        style.configure("Bench.Treeview.Heading", background=theme["card"], foreground=theme["sec"], relief="flat", borderwidth=0,
                        font=(rover.FONT_SEMIBOLD, 9))
        style.map("Bench.Treeview.Heading", background=[("active", theme["card"])])
        columns = ("model", "wer", "time", "speed", "errors")
        self.table = ttk.Treeview(inner, columns=columns, show="headings", height=8, style="Bench.Treeview", selectmode="browse")
        for key, caption, width, anchor in (("model", tr("Model"), 170, "w"), ("wer", tr("Word errors"), 100, "e"), ("time", tr("Time"), 70, "e"),
                                            ("speed", tr("× real time"), 100, "e"), ("errors", tr("Sub / del / ins"), 150, "e")):
            self.table.heading(key, text=caption, anchor=anchor)
            self.table.column(key, width=width, anchor=anchor, stretch=False)
        self.table.pack(fill="x", pady=(8, 6))
        self.table.bind("<<TreeviewSelect>>", lambda event: self.show_detail())
        self.summary = tk.Label(inner, text=tr("Run the benchmark to see which model to keep."), bg=theme["card"], fg=theme["sec"],
                                font=(rover.FONT_REGULAR, 9), anchor="w", justify="left", wraplength=560)
        self.summary.pack(fill="x")
        self.detail = tk.Text(inner, width=70, height=13, wrap="word", relief="flat", bg=theme["panel"], fg=theme["text"], highlightthickness=1,
                              highlightbackground=theme["border"], font=(rover.FONT_REGULAR, 10), padx=8, pady=6, state="disabled")
        self.detail.tag_configure("bad", foreground=theme["err"], font=(rover.FONT_SEMIBOLD, 10))
        self.detail.tag_configure("expected", foreground=theme["err"], font=(rover.FONT_REGULAR, 8))
        self.detail.tag_configure("extra", foreground=theme["accent"], underline=True)
        self.detail.tag_configure("dim", foreground=theme["sec"])
        self.detail.pack(fill="x", pady=(8, 0))
        self.small_button(inner, tr("Copy results"), self.copy_results).pack(anchor="e", pady=(8, 0))

    # -- actions
    def set_reference(self, text: str):
        self.reference.delete("1.0", "end")
        self.reference.insert("1.0", text)

    def load_text(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("Text", "*.txt *.md"), ("All", "*.*")])
        if path:
            self.set_reference(Path(path).read_text(encoding="utf-8", errors="replace"))

    def load_audio(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("WAV", "*.wav")])
        if path:
            try:
                self.audio = load_wav(path)
            except (OSError, ValueError, wave.Error) as exc:
                self.status.configure(text=str(exc), fg=self.theme["err"])
                return
            self.recording_label.configure(text=tr("Loaded {seconds} s of audio").format(seconds=f"{len(self.audio) / SAMPLE_RATE:.1f}"))

    def toggle_record(self):
        import sounddevice as sd

        if self.recording:
            self.stream.stop()
            self.stream.close()
            self.recording = False
            self.record_button.configure(text="●  " + tr("Record"))
            if self.chunks:
                self.audio = np.concatenate(self.chunks)
                self.recording_label.configure(text=tr("Recorded {seconds} s").format(seconds=f"{len(self.audio) / SAMPLE_RATE:.1f}"))
            self.meter.delete("all")
            return
        self.chunks, self.audio = [], None

        def callback(indata, frames, info, status):
            self.chunks.append(indata[:, 0].copy())
            self.level = float(np.max(np.abs(indata)))

        try:
            self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback)
            self.stream.start()
        except (sd.PortAudioError, OSError) as exc:
            self.status.configure(text=tr("Microphone unavailable: {detail}").format(detail=str(exc)[:100]), fg=self.theme["err"])
            return
        self.recording, self.started = True, time.monotonic()
        self.record_button.configure(text="■  " + tr("Stop"))

    def run(self):
        reference = self.reference.get("1.0", "end-1c").strip()
        chosen = [item for item in self.items if self.checks[item["id"]].get()]
        problem = (tr("Type or load the text you will read first.") if not reference else
                   tr("Record yourself reading it, or load an audio file.") if self.audio is None else
                   tr("Tick at least one model.") if not chosen else "")
        if problem or self.running:
            self.status.configure(text=problem, fg=self.theme["err"])
            return
        self.results = {}
        self.table.delete(*self.table.get_children())
        self.running = True
        self.run_button.configure(state="disabled")
        downloads = [i["label"] for i in chosen if i["kind"] == "local" and not i["ready"]]
        self.status.configure(text=tr("Running… {note}").format(note=(tr("downloading {names} first") .format(names=", ".join(downloads))
                                                                        if downloads else "")), fg=self.theme["sec"])
        audio, language, strip = self.audio, self.language, self.strip.get()
        for item in chosen:
            self.table.insert("", "end", iid=item["id"], values=(item["label"], "…", "", "", ""))

        def work():
            benchmark(chosen, audio, language, reference, strip, lambda result: self.inbox.put(("result", result)))
            self.inbox.put(("done", None))

        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        if self.recording:
            seconds = time.monotonic() - self.started
            self.recording_label.configure(text=tr("Recording… {seconds} s").format(seconds=f"{seconds:.1f}"))
            self.meter.delete("all")
            self.meter.create_rectangle(0, 0, 90 * min(1.0, self.level * 4), 10, fill=self.theme["rec"], width=0)
            if seconds > 120:
                self.toggle_record()
        try:
            while True:
                kind, payload = self.inbox.get_nowait()
                if kind == "result":
                    self.add_result(payload)
                else:
                    self.finish()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def add_result(self, result: dict):
        self.results[result["id"]] = result
        seconds = len(self.audio) / SAMPLE_RATE
        if result["wer"] is None:
            values = (result["name"], tr("failed"), "", "", result["error"][:30])
        else:
            s = result["score"]
            values = (result["name"], f"{result['wer'] * 100:.1f} %", f"{result['seconds']:.2f} s", f"{seconds / result['seconds']:.1f}×",
                      f"{s.substitutions} / {s.deletions} / {s.insertions}")
        self.table.item(result["id"], values=values)

    def finish(self):
        self.running = False
        self.run_button.configure(state="normal")
        ordered = sorted(self.results.values(), key=lambda r: (r["wer"] is None, r["wer"] or 0, r["seconds"] or 0))
        for position, result in enumerate(ordered):
            self.table.move(result["id"], "", position)
        pick = scoring.recommend([{"name": r["name"], "wer": r["wer"], "seconds": r["seconds"]} for r in self.results.values()])
        if pick:
            self.summary.configure(text=tr("Most accurate: {a}   ·   Fastest: {s}   ·   Best balance: {b} (the fastest within 2 points of the best)")
                                   .format(a=pick["accuracy"], s=pick["speed"], b=pick["balanced"]), fg=self.theme["text"])
        self.status.configure(text=tr("Done."), fg=self.theme["ok"])
        if ordered:
            self.table.selection_set(ordered[0]["id"])

    def show_detail(self):
        selection = self.table.selection()
        result = self.results.get(selection[0]) if selection else None
        box = self.detail
        box.configure(state="normal")
        box.delete("1.0", "end")
        if result is None:
            box.configure(state="disabled")
            return
        if result["error"]:
            box.insert("end", result["error"], "bad")
        else:
            box.insert("end", tr("Transcript") + "\n", "dim")
            box.insert("end", result["text"] + "\n\n")
            box.insert("end", tr("Compared with your text") + "\n", "dim")
            for op, ref, hyp in result["score"].ops:
                if op == "ok":
                    box.insert("end", hyp + " ")
                elif op == "sub":
                    box.insert("end", hyp, "bad")
                    box.insert("end", f"({ref}) ", "expected")
                elif op == "del":
                    box.insert("end", f"[{ref}] ", "expected")
                else:
                    box.insert("end", hyp + " ", "extra")
            missed = result["score"].missed
            if missed:
                box.insert("end", "\n\n" + tr("Missed or wrong") + ": ", "dim")
                box.insert("end", ", ".join(dict.fromkeys(missed)))
            if result["load"] > 0.5:
                box.insert("end", "\n\n" + tr("Model load time (not counted above): {seconds} s").format(seconds=f"{result['load']:.1f}"), "dim")
        box.configure(state="disabled")

    def copy_results(self):
        if self.results and self.audio is not None:
            self.root.clipboard_clear()
            self.root.clipboard_append(markdown(list(self.results.values()), len(self.audio) / SAMPLE_RATE))
            self.status.configure(text=tr("Copied · paste with Ctrl+V"), fg=self.theme["ok"])

    def close(self):
        if self.recording:
            self.stream.stop()
            self.stream.close()
        self.root.destroy()

    def run_loop(self):
        self.root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--audio", help="a WAV file (skips the window)")
    parser.add_argument("--text", help="the text that was read, in a file")
    parser.add_argument("--models", default="", help="comma separated: tiny,base,small,turbo,medium,large-v3,openai,groq")
    parser.add_argument("--lang", default="en", choices=("fr", "en"))
    parser.add_argument("--strip-accents", action="store_true")
    args = parser.parse_args()
    if args.audio and args.text:
        return cli(args)
    Window().run_loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
