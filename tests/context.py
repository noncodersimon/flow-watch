"""Shared test setup - puts adapters/ on the import path.

Adapters import each other as flat modules (from common import ...), so
the adapters directory itself has to be importable, not just the repo root.
"""

import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ADAPTERS_DIR = os.path.join(REPO_ROOT, "adapters")
DATA_DIR = os.path.join(REPO_ROOT, "data")

if ADAPTERS_DIR not in sys.path:
    sys.path.insert(0, ADAPTERS_DIR)
