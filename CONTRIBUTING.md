# Contributing

Thanks for helping! The project is small on purpose.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q tests
```

The unit tests need no display and no microphone; the same tests run in CI on Windows with Python 3.10 and 3.12.
Changes to windows, hover or animation behaviour should also pass the desktop checks in `tests/gui/` on a real session.

## Guidelines

- **UI text is English in the code** and goes through `tr("…")`; add the French translation in `i18n.py` (a test fails if one is missing).
- Keep the drawing code in `ui_kit.py` free of Tk so it stays unit-testable.
- No third-party artwork in the repository, and no personal data (paths, accounts, prompts) in code, tests or screenshots.
  `tools/make_screenshots.py` renders documentation images on a clean wallpaper with demo data; use it instead of screen captures.
- Screenshots: run `tools/make_screenshots.py` for `en` and `fr` (dark, plus light for the theme picture).

## Ideas that would be welcome

- More mascot reactions (for example a low-quota warning), or an importer for other character formats.
- Other quota sources behind the `quotas_url` JSON shape.
- macOS / Linux ports of `windows.py` and `layered.py`.
