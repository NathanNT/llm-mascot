"""Downloads classic animated assistants (Clippy, Merlin, Genie…) from the public clippy.js repository.

    python tools/get_agents.py --list
    python tools/get_agents.py Clippy Merlin

These characters are Microsoft's artwork. They are NOT part of this project and are never committed to it:
this script only fetches them onto your own machine, for your own use, after you confirm.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
REPO = "clippyjs/clippy.js"
API = f"https://api.github.com/repos/{REPO}/contents/agents"
FILES = ("agent.js", "map.png")


def available() -> list[str]:
    reply = requests.get(API, timeout=15, headers={"User-Agent": "llm-mascot"})
    reply.raise_for_status()
    return sorted(item["name"] for item in reply.json() if item["type"] == "dir")


def download(name: str, target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    total = 0
    for filename in FILES:
        info = requests.get(f"{API}/{name}/{filename}", timeout=15, headers={"User-Agent": "llm-mascot"})
        info.raise_for_status()
        data = requests.get(info.json()["download_url"], timeout=120).content
        (target / filename).write_bytes(data)
        total += len(data)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="characters to download, e.g. Clippy Merlin")
    parser.add_argument("--list", action="store_true", help="list the available characters")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation question")
    args = parser.parse_args()

    names = available()
    if args.list or not args.names:
        print("Available:", ", ".join(names))
        return 0
    wanted = []
    for name in args.names:
        match = next((n for n in names if n.lower() == name.lower()), None)
        if match is None:
            print(f"Unknown character '{name}'. Available: {', '.join(names)}")
            return 1
        wanted.append(match)

    print(f"Source: github.com/{REPO}  ->  {ROOT / 'mascots'}")
    print("These characters are Microsoft's artwork, downloaded for your personal use only.")
    if not args.yes and input("Download them? [y/N] ").strip().lower() not in ("y", "yes", "o", "oui"):
        print("Cancelled.")
        return 1
    for name in wanted:
        size = download(name, ROOT / "mascots" / name)
        print(f"{name}: {size / 1024 / 1024:.1f} MB")
    print("Done. Open the mascot menu > Customize and pick the character.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
