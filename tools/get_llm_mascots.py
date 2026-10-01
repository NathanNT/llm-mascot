"""Download community mascots of AI models into the `mascots/` folder (nothing is downloaded until you run this).

    python tools/get_llm_mascots.py --list
    python tools/get_llm_mascots.py claude deepseek capybara
    python tools/get_llm_mascots.py --all

These are fan-made Codex "pets" (animated sprite sheets that Rover already plays: rest, wave, run, jump, fail…) from two public
galleries: awesome-codex-pet (https://github.com/legeling/awesome-codex-pet) and Petdex (https://petdex.dev, repository
https://github.com/crafter-station/petdex). They are the work of their authors, under the licence each one states (several are
non-commercial) or none at all, so they are kept on your PC and never committed to this repository. The author, licence and source
of every file you download are written to `mascots/CREDITS.txt`.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
GALLERY = "https://raw.githubusercontent.com/legeling/awesome-codex-pet/main/pets/{pet}/"
PETDEX_MANIFEST = "https://petdex.dev/api/manifest"
TRUSTED_HOSTS = {"raw.githubusercontent.com", "petdex.dev", "assets.petdex.dev"}         # nothing else is ever fetched
ATLAS_SIZES = {(1536, 1872), (1536, 2288)}
MAX_BYTES = 8_000_000

# short name -> (file name in mascots/, source, id in that source, what it is)
CATALOG = {
    # Anthropic
    "claude": ("Claude", "gallery", "claude--xiangking", "a fan-made pixel Claude"),
    "clawd": ("Clawd", "petdex", "clawd-2", "a fan-made Clawd, the pixel mascot of Claude Code, with headphones"),
    "claude-chan": ("Claude-chan", "petdex", "claude-opus-scholar", "a fan-made scholar girl for Claude"),
    # OpenAI
    "chatgpt": ("ChatGPT-chan", "petdex", "chatgpt-chan", "a fan-made ChatGPT girl"),
    "froge": ("Froge", "petdex", "froge-openai-mascot", "a fan-made version of the frog mascot of OpenAI"),
    "codex": ("Codex", "petdex", "codex", "the Codex terminal prompt as a golden coin"),
    "codexy": ("Codexy", "gallery", "codexy--z19t", "an original mint-haired Codex assistant"),
    "gpt-muse": ("GPT-muse", "gallery", "gpt-muse--opask", "a tiny aqua-white GPT companion"),
    # DeepSeek, Qwen, Doubao, Llama and friends
    "deepseek": ("DeepSeek Whalechan", "gallery", "deepseek-whalechan--qimi", "a fan-made pet of DeepSeek's blue whale"),
    "deepseek-chan": ("DeepSeek-chan", "petdex", "deepseek", "another fan-made DeepSeek whale girl"),
    "capybara": ("Capybara", "gallery", "capybara-lulu--jiushu",
                 "a fan-made capybara: the animal Qwen chose as its mascot (this is not Alibaba's own artwork)"),
    "doubao": ("Doubao", "petdex", "doubao-6", "a fan-made Doubao"),
    "llama": ("Llama", "petdex", "kla-llama", "a fan-made blue llama with sunglasses, for Meta's Llama"),
    "shoggoth": ("Shoggoth", "petdex", "shoggoth", "the tentacled shoggoth that stands for a raw language model"),
    "ai-pulse": ("AI Pulse", "gallery", "ai-pulse--wxy", "a friendly blue robot for any assistant"),
}
_manifest: dict[str, dict] | None = None


def trusted(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in TRUSTED_HOSTS:
        raise SystemExit(f"refusing to fetch {url}: not one of {', '.join(sorted(TRUSTED_HOSTS))}")
    return url


def fetch(url: str) -> bytes:
    response = requests.get(trusted(url), timeout=60)
    response.raise_for_status()
    if len(response.content) > MAX_BYTES:
        raise SystemExit(f"{url}: {len(response.content) / 1e6:.1f} MB is more than a sprite sheet should be")
    return response.content


def petdex_entry(slug: str) -> dict:
    global _manifest
    if _manifest is None:
        _manifest = {pet["slug"]: pet for pet in json.loads(fetch(PETDEX_MANIFEST).decode("utf-8"))["pets"]}
    if slug not in _manifest:
        raise SystemExit(f"{slug}: not in the Petdex manifest any more")
    return _manifest[slug]


def source_of(source: str, pet: str) -> tuple[str, dict]:
    """The sprite sheet's address and what is known about its author and licence."""
    if source == "gallery":
        base = GALLERY.format(pet=pet)
        try:
            meta = json.loads(fetch(base + "submission.json").decode("utf-8"))
        except (requests.RequestException, ValueError):
            meta = {}
        author = meta.get("author")
        author = author.get("name") if isinstance(author, dict) else author
        return base + "spritesheet.webp", {"author": author, "license": meta.get("license")}
    entry = petdex_entry(pet)
    return entry["spritesheetUrl"], {"author": entry.get("submittedBy"), "license": None}


def install(short: str) -> None:
    name, source, pet, about = CATALOG[short]
    url, meta = source_of(source, pet)
    sheet = fetch(url)
    image = Image.open(io.BytesIO(sheet))
    if image.size not in ATLAS_SIZES:
        raise SystemExit(f"{pet}: unexpected sprite sheet size {image.size}, not installed")
    target = ROOT / "mascots" / f"{name}.webp"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(sheet)
    credit = (f"{name}: {about}. Author: {meta['author'] or 'see the source'}. Licence: {meta['license'] or 'not stated by the gallery: personal use only'}. "
              f"Source: {url} (sha256 {hashlib.sha256(sheet).hexdigest()[:16]}…)\n")
    credits = target.parent / "CREDITS.txt"
    lines = credits.read_text(encoding="utf-8").splitlines(keepends=True) if credits.exists() else []
    credits.write_text("".join(line for line in lines if not line.startswith(f"{name}:")) + credit, encoding="utf-8")      # one line per mascot, however often you run this
    print(f"installed {target.name}  ({len(sheet) / 1e6:.1f} MB)  {about}\n  {credit.strip()}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="which ones to download: " + ", ".join(CATALOG))
    parser.add_argument("--list", action="store_true", help="show what is available and exit")
    parser.add_argument("--all", action="store_true", help="download every mascot of the list")
    args = parser.parse_args()
    if args.list or not (args.names or args.all):
        for short, (name, source, pet, about) in CATALOG.items():
            print(f"{short:14} {name}: {about}  [{source}]")
        return 0
    names = list(CATALOG) if args.all else args.names
    unknown = [name for name in names if name not in CATALOG]
    if unknown:
        print("unknown:", ", ".join(unknown), "(try --list)")
        return 2
    failed = []
    for short in names:
        try:
            install(short)
        except (requests.RequestException, OSError) as exc:
            failed.append(short)
            print(f"{short}: {exc}")
    print("\nRestart Rover, then pick them in the customization window." if len(failed) < len(names) else "\nNothing was installed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
