"""A stand-in for a chat app: a text box holding a draft prompt. Reports its window handle and content to a file."""
import json
import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import layered

report = Path(sys.argv[1])
root = tk.Tk()
root.title("Fake chat app")
root.geometry("420x160+700+120")
box = tk.Text(root, width=50, height=6, font=("Segoe UI", 11))
box.pack(fill="both", expand=True)
box.insert("1.0", "My draft prompt that must survive. ")
box.focus_force()


def publish():
    report.write_text(json.dumps({"hwnd": layered.toplevel_hwnd(root), "text": box.get("1.0", "end-1c")}), encoding="utf-8")
    if Path(str(report) + ".stop").exists():
        root.destroy()
    else:
        root.after(100, publish)


root.after(300, publish)
root.after(40000, root.destroy)
root.mainloop()
