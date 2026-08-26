from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6 import QtWidgets

from bildfahrplan.profile import RouteProfile
from .collector_adapter import CollectorAdapter, REPOSITORY_ROOT
from .main_window import MainWindow


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="StellwerkSim Bildfahrplan V0.3.5")
    result.add_argument("--state", type=Path, help="Collector-State offline laden")
    result.add_argument("--profile", type=Path, default=REPOSITORY_ROOT / "config/routes/example.json")
    result.add_argument("--host", default="127.0.0.1")
    result.add_argument("--port", type=int, default=3691)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    state = args.state or REPOSITORY_ROOT / "Schnittstellentest/sts_collector_state.json"
    adapter = CollectorAdapter(state, offline=args.state is not None)
    application = QtWidgets.QApplication(sys.argv[:1])
    window = MainWindow(adapter, RouteProfile.load(args.profile))
    window.show()
    adapter.start(args.host, args.port)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
