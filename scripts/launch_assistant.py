#!/usr/bin/env python3
"""Launch the localhost-only sample follow-up assistant."""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from assistant import bootstrap


if __name__ == "__main__":
    raise SystemExit(bootstrap.main())
