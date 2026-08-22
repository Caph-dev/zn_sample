"""Allow full-suite discovery to resolve the product ``assistant`` package."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
__path__.append(str(PROJECT_ROOT / "assistant"))
