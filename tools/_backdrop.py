"""A plain, topmost wallpaper window used only while taking documentation screenshots (hides the real desktop)."""
import sys
import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

x, y, w, h, dark = (int(v) for v in sys.argv[1:6])
root = tk.Tk()
root.overrideredirect(True)
root.attributes("-topmost", True)
root.geometry(f"{w}x{h}+{x}+{y}")
top, bottom = ((34, 38, 58), (12, 13, 20)) if dark else ((247, 240, 228), (226, 214, 192))
image = Image.new("RGB", (w, h))
draw = ImageDraw.Draw(image)
for row in range(h):
    t = row / h
    draw.line((0, row, w, row), fill=tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
for gx in range(0, w, 40):
    draw.line((gx, 0, gx, h), fill=tuple(round(c + (10 if dark else -8)) for c in (top if False else (40, 44, 64) if dark else (240, 230, 212))), width=1)
for gy in range(0, h, 40):
    draw.line((0, gy, w, gy), fill=(40, 44, 64) if dark else (240, 230, 212), width=1)
photo = ImageTk.PhotoImage(image)
tk.Label(root, image=photo, bd=0).pack()
root.mainloop()
