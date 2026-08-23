#!/usr/bin/env python3
"""Isolated path bootstrap for Telegram migration CLI operations."""

from pathlib import Path
import runpy
import sys


BACKEND = Path("/workspace/backend")
if not BACKEND.is_dir() or BACKEND.is_symlink():
    raise SystemExit("Telegram backend path is unavailable")
sys.path.insert(0, str(BACKEND))
runpy.run_module("seiche.telegram_migration", run_name="__main__")
