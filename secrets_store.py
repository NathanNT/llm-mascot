"""Keep an API key encrypted for the current Windows user (DPAPI): settings.json never holds it in clear text."""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes

PREFIX = "dpapi:"


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_crypt32.CryptProtectData.argtypes = (ctypes.POINTER(DataBlob), wintypes.LPCWSTR, ctypes.POINTER(DataBlob), ctypes.c_void_p,
                                      ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob))
_crypt32.CryptProtectData.restype = wintypes.BOOL
_crypt32.CryptUnprotectData.argtypes = (ctypes.POINTER(DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(DataBlob),
                                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob))
_crypt32.CryptUnprotectData.restype = wintypes.BOOL
_kernel32.LocalFree.argtypes = (ctypes.c_void_p,)


def _input(data: bytes):
    buffer = ctypes.create_string_buffer(data, len(data))
    return DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _take(blob: DataBlob) -> bytes:
    data = ctypes.string_at(blob.pbData, blob.cbData)
    _kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))
    return data


def protect(text: str) -> str:
    """Encrypt `text` for this Windows account; returns 'dpapi:<base64>' (or '' for empty text)."""
    if not text:
        return ""
    source, _keep = _input(text.encode("utf-8"))
    result = DataBlob()
    if not _crypt32.CryptProtectData(ctypes.byref(source), "LLM Mascot", None, None, None, 0, ctypes.byref(result)):
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
    return PREFIX + base64.b64encode(_take(result)).decode("ascii")


def unprotect(stored: str) -> str:
    """Decrypt a value made by protect(); returns '' when it is empty, foreign or from another Windows account."""
    if not stored.startswith(PREFIX):
        return ""
    try:
        raw = base64.b64decode(stored[len(PREFIX):], validate=True)
    except ValueError:
        return ""
    source, _keep = _input(raw)
    result = DataBlob()
    if not _crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        return ""
    return _take(result).decode("utf-8", errors="replace")
