"""Per-pixel-alpha windows (UpdateLayeredWindow): smooth edges with no colour-key halo."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

import numpy as np
from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0
AC_SRC_ALPHA = 1
GA_ROOT = 2
BI_RGB = 0
DIB_RGB_COLORS = 0


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_long)
user32.SetWindowLongW.restype = ctypes.c_long
user32.GetDC.argtypes = (wintypes.HWND,)
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
user32.UpdateLayeredWindow.argtypes = (wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                       ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                       wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD)
user32.UpdateLayeredWindow.restype = wintypes.BOOL
gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = (wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
gdi32.DeleteDC.argtypes = (wintypes.HDC,)


class Bitmap:
    """A premultiplied 32-bit BGRA bitmap ready for UpdateLayeredWindow."""

    def __init__(self, image: Image.Image):
        image = image.convert("RGBA")
        self.width, self.height = image.size
        premultiplied = np.asarray(image.convert("RGBa"), dtype=np.uint8)
        data = np.ascontiguousarray(premultiplied[..., [2, 1, 0, 3]])
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = self.width
        info.bmiHeader.biHeight = -self.height  # top-down
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        self.dc = gdi32.CreateCompatibleDC(None)
        self.handle = gdi32.CreateDIBSection(self.dc, ctypes.byref(info), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
        ctypes.memmove(bits, data.ctypes.data, data.nbytes)
        self._previous = gdi32.SelectObject(self.dc, self.handle)

    def close(self) -> None:
        if self.dc:
            gdi32.SelectObject(self.dc, self._previous)
            gdi32.DeleteObject(self.handle)
            gdi32.DeleteDC(self.dc)
            self.dc = None


def toplevel_hwnd(widget) -> int:
    widget.update_idletasks()
    return user32.GetAncestor(widget.winfo_id(), GA_ROOT) or widget.winfo_id()


def enable(widget, no_activate: bool = False) -> int:
    """Turn a Tk top-level into a layered window; returns its handle."""
    hwnd = toplevel_hwnd(widget)
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED | WS_EX_TOOLWINDOW
    if no_activate:
        style |= WS_EX_NOACTIVATE
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    return hwnd


def update(hwnd: int, bitmap: Bitmap, x: int | None = None, y: int | None = None, alpha: int = 255) -> bool:
    """Draw `bitmap` into the layered window (optionally moving it) with a constant fade `alpha` (0-255)."""
    position = wintypes.POINT(x, y) if x is not None and y is not None else None
    size = wintypes.SIZE(bitmap.width, bitmap.height)
    source = wintypes.POINT(0, 0)
    blend = BLENDFUNCTION(AC_SRC_OVER, 0, max(0, min(255, alpha)), AC_SRC_ALPHA)
    return bool(user32.UpdateLayeredWindow(hwnd, None, ctypes.byref(position) if position else None, ctypes.byref(size),
                                           bitmap.dc, ctypes.byref(source), 0, ctypes.byref(blend), ULW_ALPHA))
