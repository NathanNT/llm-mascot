"""Unit tests run anywhere Windows-free code can: they never import the Tk application itself."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
