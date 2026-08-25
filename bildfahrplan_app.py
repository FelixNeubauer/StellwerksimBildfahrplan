"""Repositoryweiter Entry Point fuer die PySide6-Anwendung."""

from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parent / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from app.main import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
