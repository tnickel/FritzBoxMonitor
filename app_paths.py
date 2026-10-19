"""
Base directory resolution for source runs and PyInstaller-frozen runs.

- get_base_dir(): user-writable location for config/data/logs — next to the
  .exe when frozen, next to the project files in source runs.
- get_resource_dir(): bundled read-only resources (ui/) — the onefile
  extraction dir (sys._MEIPASS) when frozen, the project dir in source runs.
"""

import sys
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def get_resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).parent
