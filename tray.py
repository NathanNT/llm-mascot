"""A notification-area icon (the arrow next to the clock: "Show hidden icons"), with no extra dependency.

Windows draws tray icons for a window that owns them, so the icon lives on a hidden message-only window of its own, run
on its own thread. What the user does with it (click, menu) comes back through `events`: "show", "hide", "dictate",
"settings" or "quit". A message-only window does not receive the broadcast Windows sends when Explorer restarts, so
`ensure()` (called from time to time) puts the icon back whenever Windows has lost it.
"""

from __future__ import annotations

import ctypes
import itertools
import queue
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Callable

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_NULL, WM_DESTROY, WM_CLOSE, WM_APP = 0x0000, 0x0002, 0x0010, 0x8000
WM_TRAY, WM_ENSURE = WM_APP + 1, WM_APP + 2
WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
HWND_MESSAGE = -3
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
MF_STRING, MF_SEPARATOR = 0x0, 0x800
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x2, 0x100
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                ("uTimeoutOrVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", wintypes.HICON)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = (ctypes.POINTER(WNDCLASSW),)
user32.RegisterClassW.restype = wintypes.ATOM
user32.UnregisterClassW.argtypes = (wintypes.LPCWSTR, wintypes.HINSTANCE)
user32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = (wintypes.HWND,)
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostQuitMessage.argtypes = (ctypes.c_int,)
user32.LoadImageW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT)
user32.LoadImageW.restype = wintypes.HANDLE
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = (wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR)
user32.TrackPopupMenu.argtypes = (wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.LPVOID)
user32.DestroyMenu.argtypes = (wintypes.HMENU,)
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.RegisterWindowMessageW.argtypes = (wintypes.LPCWSTR,)
user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
SM_CXSMICON = 49
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
shell32.Shell_NotifyIconW.argtypes = (wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
shell32.Shell_NotifyIconW.restype = wintypes.BOOL

_names = itertools.count(1)


class TrayIcon:
    """`show()` puts the icon in the notification area, `hide()` takes it away; user actions arrive in `events`."""

    def __init__(self, icon_path: Path | str, tooltip: str, labels: Callable[[], dict[str, str]], is_hidden: Callable[[], bool] = lambda: False):
        self.icon_path, self.tooltip, self.labels, self.is_hidden = str(icon_path), tooltip[:127], labels, is_hidden
        self.events: queue.Queue[str] = queue.Queue()
        self.hwnd = None
        self.thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._proc = None
        self._icon = None
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")      # sent again when Explorer restarts

    # -- public ------------------------------------------------------------------------------------------------
    def showing(self) -> bool:
        return bool(self.hwnd) and self.thread is not None and self.thread.is_alive()

    def show(self) -> bool:
        if self.showing():
            return True
        self._ready.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self._ready.wait(3)
        return self.showing()

    def hide(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        if self.thread is not None:
            self.thread.join(1.5)
        self.hwnd = None

    def ensure(self) -> None:
        """Put the icon back if Windows lost it (Explorer restarted, the taskbar was rebuilt)."""
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_ENSURE, 0, 0)

    def present(self) -> bool:
        """True while Windows really has the icon (a no-op update succeeds only for an existing icon)."""
        if not self.hwnd:
            return False
        data = self._data()
        return bool(shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data)))

    # -- the icon's own window ---------------------------------------------------------------------------------
    def _data(self) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd, data.uID = self.hwnd, 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAY
        data.hIcon = self._icon
        data.szTip = self.tooltip
        return data

    def _add(self) -> bool:
        data = self._data()
        return bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)))

    def _ensure(self) -> None:
        if not shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._data())):      # modifying an icon Windows does not have fails
            self._add()

    def _run(self) -> None:
        name = f"LLMMascotTray{next(_names)}"
        instance = kernel32.GetModuleHandleW(None)
        self._proc = WNDPROC(self._window_proc)
        wc = WNDCLASSW()
        wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = self._proc, instance, name
        if not user32.RegisterClassW(ctypes.byref(wc)):
            self._ready.set()
            return
        try:
            size = user32.GetSystemMetrics(SM_CXSMICON) or 16                       # 16 px at 100 %, larger on a scaled screen
            self._icon = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, size, size, LR_LOADFROMFILE)
            self.hwnd = user32.CreateWindowExW(0, name, "LLM Mascot", 0, 0, 0, 0, 0, HWND_MESSAGE, None, instance, None)
            if self.hwnd:
                self._add()
            self._ready.set()
            message = wintypes.MSG()
            while self.hwnd and user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            self._ready.set()
            self.hwnd = None
            user32.UnregisterClassW(name, instance)

    def _window_proc(self, hwnd, message, wparam, lparam):
        if message == WM_TRAY:
            event = lparam & 0xFFFF
            if event == WM_LBUTTONUP:
                self.events.put("show")
            elif event == WM_RBUTTONUP:
                self._menu(hwnd)
            return 0
        if message == WM_CLOSE:
            data = self._data()
            data.hWnd = hwnd
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
            user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            self.hwnd = None
            user32.PostQuitMessage(0)
            return 0
        if message == WM_ENSURE or message == self._taskbar_created:
            self._ensure()
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _menu(self, hwnd) -> None:
        menu = user32.CreatePopupMenu()
        labels = self.labels()
        entries = [(1, "show" if self.is_hidden() else "hide"), (2, "dictate"), (3, "settings")]
        for command, key in entries:
            user32.AppendMenuW(menu, MF_STRING, command, labels[key])
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, 4, labels["quit"])
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(hwnd)                      # without this the menu would not close when you click elsewhere
        choice = user32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, point.x, point.y, 0, hwnd, None)
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        if choice in (1, 2, 3, 4):
            self.events.put({1: entries[0][1], 2: "dictate", 3: "settings", 4: "quit"}[choice])
