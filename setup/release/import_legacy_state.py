#!/usr/bin/env python3
"""Technical-operator entrypoint; no imports outside the standard library."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# A dry-run must not even create __pycache__ in the source or release tree.
sys.dont_write_bytecode = True
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant.services.local_state_import import import_local_state  # noqa: E402


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect same-machine legacy state; default is dry-run.")
    parser.add_argument("--source", required=True, type=Path, help="Explicit absolute legacy project root")
    parser.add_argument("--apply", action="store_true", help="Local file writes only; currently gated on plan 009")
    parser.add_argument("--yes", action="store_true", help="Confirm local import, not business execution")
    options = parser.parse_args(arguments)
    report = import_local_state(options.source, apply=options.apply, yes=options.yes)
    print(json.dumps(report.as_dict(), ensure_ascii=True, indent=2))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
