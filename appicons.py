"""Icons for shortcut buttons: Windows shell icons for local files, favicons for web addresses."""

from __future__ import annotations

import ctypes
import hashlib
import re
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urljoin, urlparse

import numpy as np
import requests
from PIL import Image

HERE = Path(__file__).resolve().parent
ICON_DIR = HERE / "assets" / "icons"

shell32 = ctypes.WinDLL("shell32")
ole32 = ctypes.WinDLL("ole32")
user32 = ctypes.WinDLL("user32")
gdi32 = ctypes.WinDLL("gdi32")

SHGFI_ICON = 0x100
SHGFI_LARGEICON = 0x0
DI_NORMAL = 0x3


class SHFILEINFO(ctypes.Structure):
    _fields_ = [("hIcon", wintypes.HICON), ("iIcon", ctypes.c_int), ("dwAttributes", wintypes.DWORD),
                ("szDisplayName", wintypes.WCHAR * 260), ("szTypeName", wintypes.WCHAR * 80)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


shell32.SHGetFileInfoW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(SHFILEINFO), wintypes.UINT, wintypes.UINT)
shell32.SHGetFileInfoW.restype = ctypes.c_size_t
user32.DrawIconEx.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON, ctypes.c_int, ctypes.c_int,
                              wintypes.UINT, wintypes.HBRUSH, wintypes.UINT)
user32.DrawIconEx.restype = wintypes.BOOL
user32.DestroyIcon.argtypes = (wintypes.HICON,)
gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = (wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                   wintypes.HANDLE, wintypes.DWORD)
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
gdi32.DeleteDC.argtypes = (wintypes.HDC,)


def _draw(hicon: int, size: int, background: int) -> np.ndarray:
    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth, header.biHeight = size, -size
    header.biPlanes, header.biBitCount = 1, 32
    bits = ctypes.c_void_p()
    dc = gdi32.CreateCompatibleDC(None)
    bitmap = gdi32.CreateDIBSection(dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
    previous = gdi32.SelectObject(dc, bitmap)
    ctypes.memset(bits, background, size * size * 4)
    user32.DrawIconEx(dc, 0, 0, hicon, size, size, 0, None, DI_NORMAL)
    data = np.frombuffer(ctypes.string_at(bits, size * size * 4), dtype=np.uint8).reshape(size, size, 4).copy()
    gdi32.SelectObject(dc, previous)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(dc)
    return data[..., :3].astype(np.float64)        # BGR


def shell_icon(path: str, size: int = 32) -> Image.Image | None:
    """The icon Explorer shows for a file, folder or shortcut, with real transparency."""
    ole32.CoInitialize(None)          # the shell needs COM, including on worker threads
    info = SHFILEINFO()
    if not shell32.SHGetFileInfoW(path, 0, ctypes.byref(info), ctypes.sizeof(info), SHGFI_ICON | SHGFI_LARGEICON):
        return None
    try:
        on_black, on_white = _draw(info.hIcon, size, 0x00), _draw(info.hIcon, size, 0xFF)
    finally:
        user32.DestroyIcon(info.hIcon)
    alpha = np.clip(255 - (on_white - on_black).mean(axis=2), 0, 255)
    safe = np.maximum(alpha, 1)[..., None]
    color = np.clip(on_black * 255 / safe, 0, 255)[..., ::-1]      # back to RGB, un-premultiplied
    rgba = np.dstack([color, alpha]).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def icon_key(target: str) -> str:
    return hashlib.sha1(target.encode("utf-8")).hexdigest()[:16]


def favicon(url: str) -> Image.Image | None:
    """Fetch the favicon of the address the user added (and nothing else)."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    headers = {"User-Agent": "RoverAssistant"}
    candidates = []
    try:
        page = requests.get(url, timeout=3, headers=headers)
        for tag in re.findall(r"<link[^>]+>", page.text[:60000], re.I):
            if re.search(r'rel=["\'][^"\']*icon', tag, re.I):
                href = re.search(r'href=["\']([^"\']+)', tag, re.I)
                if href:
                    candidates.append(urljoin(url, href.group(1)))
    except requests.RequestException:
        pass
    candidates.append(origin + "/favicon.ico")
    for candidate in candidates[:4]:
        try:
            reply = requests.get(candidate, timeout=3, headers=headers)
            if reply.ok and reply.content and len(reply.content) < 2_000_000:
                from io import BytesIO
                image = Image.open(BytesIO(reply.content))
                return image.convert("RGBA")
        except (requests.RequestException, OSError, ValueError, AttributeError, SyntaxError):
            continue
    return None


def save_icon(target: str, image: Image.Image) -> str:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    name = icon_key(target) + ".png"
    image.save(ICON_DIR / name)
    return name


def load_icon(name: str) -> Image.Image | None:
    path = ICON_DIR / Path(name).name
    try:
        return Image.open(path).convert("RGBA")
    except OSError:
        return None
