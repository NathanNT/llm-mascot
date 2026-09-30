"""Manual check (needs a Windows desktop): text is typed at the caret of the target window.

It never types into another application: insert_text() refuses unless the target window really is in the foreground.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import tkinter as tk

import ctypes

import layered
from windows import insert_text

user32 = ctypes.windll.user32


def main():
    root = tk.Tk()
    root.title("LLM Mascot insertion test")
    field = tk.Text(root, width=40, height=4)
    field.pack()
    field.insert("1.0", "Existing prompt. ")
    field.focus_force()
    user32.keybd_event(0x12, 0, 0, 0)        # an Alt tap lets this test window take the focus
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(layered.toplevel_hwnd(root))
    result = {"inserted": False}

    def inject():
        hwnd = layered.toplevel_hwnd(root)
        result["inserted"] = insert_text(hwnd, "Hello, this is a test.\nSecond line.")
        root.after(250, inspect)

    def inspect():
        value = field.get("1.0", "end-1c")
        print({"send_input_ok": result["inserted"], "field_value": value})
        if result["inserted"]:
            assert value == "Existing prompt. Hello, this is a test.\nSecond line.", value
        root.destroy()

    root.after(400, inject)
    root.mainloop()


if __name__ == "__main__":
    main()
