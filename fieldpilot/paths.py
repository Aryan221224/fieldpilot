"""Where FieldPilot's data lives: the project folder, or the folder holding FieldPilot.exe."""
from __future__ import annotations

import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
# frozen: web/, vendor/, examples/, sim/, runs/ and .env sit next to FieldPilot.exe
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parents[1]
