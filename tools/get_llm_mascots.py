"""Download community mascots of AI models into the `mascots/` folder (nothing is downloaded until you run this).

    python tools/get_llm_mascots.py --list
    python tools/get_llm_mascots.py claude deepseek capybara

These are fan-made Codex "pets" (animated sprite sheets that Rover already plays: rest, wave, run, jump, fail…) from the
awesome-codex-pet gallery, https://github.com/legeling/awesome-codex-pet . They are the work of their authors, under the
licence each one states (several are non-commercial), so they are kept on your PC and never committed to this repository.
The author and licence of every file you download are written to `mascots/CREDITS.txt`.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://raw.githubusercontent.com/legeling/awesome-codex-pet/main/pets/{pet}/"
# short name -> (file name in mascots/, folder in the gallery, what it is)
CATALOG = {
    "claude": ("Claude (community)", "claude--xiangking", "a fan-made Claude pet"),
    "deepseek": ("DeepSeek Whalechan (community)", "deepseek-whalechan--qimi", "a fan-made pet of DeepSeek's blue whale"),
    "capybara": ("Capybara (Qwen's animal, community)", "capybara-lulu--jiushu",
                 "a fan-made capybara: the animal Qwen chose as its mascot (this is not Alibaba's own artwork)"),
}
ATLAS_SIZES = {(1536, 1872), (1536, 2288)}


def fetch(url: str) -> bytes:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.content


def install(short: str) -> None:
    name, pet, about = CATALOG[short]
    base = BASE.format(pet=pet)
    sheet = fetch(base + "spritesheet.webp")
    image = Image.open(io.BytesIO(sheet))
    if image.size not in ATLAS_SIZES:
        raise SystemExit(f"{pet}: unexpected sprite sheet size {image.size}, not installed")
    try:
        meta = json.loads(fetch(base + "submission.json").decode("utf-8"))
    except (requests.RequestException, ValueError):
        meta = {}
    target = ROOT / "mascots" / f"{name}.webp"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(sheet)
    author = (meta.get("author") or {}).get("name") if isinstance(meta.get("author"), dict) else meta.get("author")
    credit = (f"{name}: {about}. Author: {author or 'see the source'}. Licence: {meta.get('license') or 'see the source'}. "
              f"Source: {base}spritesheet.webp (sha256 {hashlib.sha256(sheet).hexdigest()[:16]}…)\n")
    with open(target.parent / "CREDITS.txt", "a", encoding="utf-8") as handle:
        handle.write(credit)
    print(f"installed {target.name}  ({len(sheet) / 1e6:.1f} MB)  {about}\n  {credit.strip()}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="which ones to download: " + ", ".join(CATALOG))
    parser.add_argument("--list", action="store_true", help="show what is available and exit")
    args = parser.parse_args()
    if args.list or not args.names:
        for short, (name, pet, about) in CATALOG.items():
            print(f"{short:10} {name}: {about}")
        return 0
    unknown = [name for name in args.names if name not in CATALOG]
    if unknown:
        print("unknown:", ", ".join(unknown), "(try --list)")
        return 2
    for short in args.names:
        install(short)
    print("\nRestart Rover, then pick them in the customization window.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
