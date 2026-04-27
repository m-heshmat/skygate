"""Application entry point — delegates to the CLI presentation layer."""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from skygate.presentation.cli.app import run  # noqa: E402


if __name__ == "__main__":
    run()
