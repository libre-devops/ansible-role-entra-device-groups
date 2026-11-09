"""Make the role's module importable in the unit tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "library"))
