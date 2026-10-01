"""Windows hotkey and text insertion helpers. No clipboard or Enter key is used."""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

import hotkeys

user32 = ctypes.windll.user32
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("data", INPUTUNION)]


user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.IsWindow.argtypes = (wintypes.HWND,)
user32.IsWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.PeekMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT)
user32.PeekMessageW.restype = wintypes.BOOL
user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.CreateMutexW.restype = wintypes.HANDLE
_instance_mutex = None


def acquire_single_instance() -> bool:
    global _instance_mutex
    _instance_mutex = kernel32.CreateMutexW(None, False, "Local\\RoverAssistant")
    return bool(_instance_mutex) and ctypes.get_last_error() != 183


def foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)


def desktop_bounds() -> tuple[int, int, int, int]:
    """Virtual desktop, including monitors left of or above the primary screen."""
    x, y, width, height = (user32.GetSystemMetrics(metric) for metric in (76, 77, 78, 79))
    return x, y, x + width, y + height


def place_window(window, x: int, y: int, width: int | None = None, height: int | None = None) -> None:
    """Place a Tk top-level using absolute coordinates, including negative monitors.

    Tk geometry's negative offsets mean distance from the right/bottom edge,
    so they cannot express an absolute position on a left-hand monitor.
    """
    window.update_idletasks()
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT)
    user32.SetWindowPos.restype = wintypes.BOOL
    hwnd = user32.GetAncestor(window.winfo_id(), 2) or window.winfo_id()
    flags = 0x0004 | 0x0010  # Keep stacking and keyboard focus.
    if width is None or height is None:
        flags |= 0x0001  # Keep size.
    user32.SetWindowPos(hwnd, None, x, y, width or 0, height or 0, flags)


def monitor_work_area(x: int, y: int) -> tuple[int, int, int, int]:
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
    user32.MonitorFromPoint.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MONITORINFO))
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    monitor = user32.MonitorFromPoint(wintypes.POINT(x, y), 2)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        area = info.rcWork
        return area.left, area.top, area.right, area.bottom
    return desktop_bounds()


def is_own_window(hwnd: int) -> bool:
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


def register_hotkey(spec: str = hotkeys.DEFAULT, hotkey_id: int = 1) -> bool:
    """Claim the shortcut system-wide; False when it is unusable or another program already owns it."""
    parsed = hotkeys.parse(spec)
    return bool(parsed and user32.RegisterHotKey(None, hotkey_id, parsed[0] | MOD_NOREPEAT, parsed[1]))


def pressed_modifiers() -> set[str]:
    """Which of Ctrl, Alt, Shift and Win are held down right now."""
    held = set()
    for name, codes in (("ctrl", (0x11,)), ("alt", (0x12,)), ("shift", (0x10,)), ("win", (0x5B, 0x5C))):
        if any(user32.GetAsyncKeyState(code) & 0x8000 for code in codes):
            held.add(name)
    return held


def unregister_hotkey(hotkey_id: int = 1) -> None:
    user32.UnregisterHotKey(None, hotkey_id)


def consume_hotkey(hotkey_id: int = 1) -> bool:
    message = wintypes.MSG()
    fired = False
    while user32.PeekMessageW(ctypes.byref(message), None, WM_HOTKEY, WM_HOTKEY, PM_REMOVE):
        fired = fired or message.wParam == hotkey_id
    return fired


VK_SHIFT, VK_CONTROL, VK_MENU, VK_RETURN = 0x10, 0x11, 0x12, 0x0D
CRLF, CR, LF = chr(13) + chr(10), chr(13), chr(10)
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    entry = INPUT()
    entry.type = INPUT_KEYBOARD
    entry.data.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return entry


def _send(events: list[INPUT]) -> bool:
    # SendInput accepts arrays of limited size more reliably for long prompts.
    for start in range(0, len(events), 256):
        batch = events[start:start + 256]
        array = (INPUT * len(batch))(*batch)
        if user32.SendInput(len(array), array, ctypes.sizeof(INPUT)) != len(array):
            return False
    return True


def _wait_for_modifiers_released(timeout: float = 1.5) -> None:
    """Typing while Ctrl/Alt are still down (the dictation shortcut) would trigger shortcuts instead of text."""
    end = time.monotonic() + timeout
    while time.monotonic() < end and any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (VK_CONTROL, VK_MENU, VK_SHIFT)):
        time.sleep(0.03)


def insert_text(hwnd: int, value: str) -> bool:
    """Type `value` at the caret of `hwnd`: no clipboard, no Enter key, existing text is left alone.

    Focus is only touched when `hwnd` is not already the foreground window. Line breaks are sent as Shift+Enter so
    that a multi-line prompt can never submit the message.
    """
    if not hwnd or not user32.IsWindow(hwnd) or not value:
        return False
    if foreground_window() != hwnd:
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.12)
        if foreground_window() != hwnd:
            return False
    _wait_for_modifiers_released()

    lines = value.replace(CRLF, LF).replace(CR, LF).split(LF)
    for index, line in enumerate(lines):
        if index:
            newline = [_key(VK_SHIFT), _key(VK_RETURN), _key(VK_RETURN, flags=KEYEVENTF_KEYUP), _key(VK_SHIFT, flags=KEYEVENTF_KEYUP)]
            if not _send(newline):
                return False
        units = line.encode("utf-16-le")
        events: list[INPUT] = []
        for position in range(0, len(units), 2):
            code_unit = int.from_bytes(units[position:position + 2], "little")
            events.append(_key(0, code_unit, KEYEVENTF_UNICODE))
            events.append(_key(0, code_unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        if events and not _send(events):
            return False
    return True


_gdi32 = ctypes.WinDLL("gdi32")
_user32 = ctypes.WinDLL("user32")


def round_top_corners(window, radius: int = 8) -> None:
    """Clip a borderless window to rounded top corners and square bottom ones, as Windows XP did (hard edge, no smoothing)."""
    window.update_idletasks()
    _user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    _user32.GetAncestor.restype = wintypes.HWND
    _user32.SetWindowRgn.argtypes = (wintypes.HWND, ctypes.c_void_p, wintypes.BOOL)
    _gdi32.CreateRoundRectRgn.argtypes = (ctypes.c_int,) * 6
    _gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
    hwnd = _user32.GetAncestor(window.winfo_id(), 2) or window.winfo_id()
    width, height = window.winfo_width(), window.winfo_height()
    region = _gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 2 * radius + 1, 2 * radius, 2 * radius)   # taller than the window: the bottom stays square
    _user32.SetWindowRgn(hwnd, region, True)                                                                 # the system owns the region now
