"""Find out which graphics card this PC has and whether its drivers can run the speech model (Vulkan)."""

from __future__ import annotations

import json
import re
import subprocess
import winreg
from pathlib import Path

VENDORS = {"1002": "amd", "10DE": "nvidia", "8086": "intel"}
VENDOR_NAMES = {"amd": "AMD", "nvidia": "NVIDIA", "intel": "Intel"}
DRIVER_PAGES = {
    "amd": "https://www.amd.com/en/support/download/drivers.html",
    "nvidia": "https://www.nvidia.com/Download/index.aspx",
    "intel": "https://www.intel.com/content/www/us/en/download-center/home.html",
}
IGNORED = ("basic render", "basic display", "remote display", "virtual", "hyper-v", "parsec", "displaylink")


def _powershell(script: str) -> str:
    completed = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return completed.stdout


def adapters() -> list[dict]:
    """Every real graphics adapter: {'name', 'vendor' ('amd'|'nvidia'|'intel'|''), 'driver'}."""
    script = ("Get-CimInstance Win32_VideoController | Select-Object Name, PNPDeviceID, DriverVersion, AdapterRAM | "
              "ConvertTo-Json -Compress")
    try:
        data = json.loads(_powershell(script) or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    data = [data] if isinstance(data, dict) else data
    found = []
    for entry in data:
        name = str(entry.get("Name") or "")
        if not name or any(word in name.lower() for word in IGNORED):
            continue
        match = re.search(r"VEN_([0-9A-F]{4})", str(entry.get("PNPDeviceID") or "").upper())
        found.append({"name": name, "vendor": VENDORS.get(match.group(1), "") if match else "",
                      "driver": str(entry.get("DriverVersion") or ""), "ram": int(entry.get("AdapterRAM") or 0)})
    return found


def vulkan_drivers() -> list[str]:
    """Vulkan drivers that graphics drivers have registered on this PC (empty: the GPU driver has no Vulkan support)."""
    found: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Khronos\Vulkan\Drivers") as key:
            index = 0
            while True:
                found.append(winreg.EnumValue(key, index)[0])
                index += 1
    except OSError:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}") as cls:
            for index in range(32):
                try:
                    with winreg.OpenKey(cls, f"{index:04d}") as sub:
                        value = winreg.QueryValueEx(sub, "VulkanDriverName")[0]
                        found.append(str(value[0] if isinstance(value, list) else value))
                except OSError:
                    continue
    except OSError:
        pass
    return [path for path in dict.fromkeys(found) if path]


def loader_present() -> bool:
    return Path(r"C:\Windows\System32\vulkan-1.dll").exists()


def pick_best(found: list[dict]) -> dict | None:
    """Prefer a discrete AMD/NVIDIA card over an integrated one; among equals, the one reporting the most memory."""
    if not found:
        return None
    rank = {"nvidia": 3, "amd": 3, "intel": 1, "": 0}
    return max(found, key=lambda a: (rank.get(a["vendor"], 0), a["ram"]))


def detect() -> dict:
    """{'adapters', 'best', 'vulkan_ready', 'driver_page', 'advice'} – advice is a plain-language next step or ''."""
    found = adapters()
    best = pick_best(found)
    drivers = vulkan_drivers()
    ready = bool(best) and loader_present() and bool(drivers)
    advice = ""
    if not best:
        advice = "no_gpu"
    elif not loader_present() or not drivers:
        advice = "install_driver"
    return {"adapters": found, "best": best, "vulkan_ready": ready, "advice": advice,
            "driver_page": DRIVER_PAGES.get(best["vendor"], "") if best else ""}
