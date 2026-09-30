"""Manual check (needs a Windows desktop): small isolated smoke test: typing must not touch the clipboard or submit a form."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import tkinter as tk

from windows import foreground_window, insert_text


def main():
    root = tk.Tk()
    root.title("LLM Mascot insertion test")
    field = tk.Text(root, width=40, height=4)
    field.pack()
    field.focus_force()
    result = {"inserted": False}

    def inject():
        hwnd = foreground_window()
        result["inserted"] = insert_text(hwnd, "Hello, this is a test.
Second line.")
        root.after(250, inspect)

    def inspect():
        value = field.get("1.0", "end-1c")
        print({"send_input_ok": result["inserted"], "field_value": value})
        root.destroy()

    root.after(250, inject)
    root.mainloop()


if __name__ == "__main__":
    main()
