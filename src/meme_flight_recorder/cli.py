from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from .config import load_settings
from .journal import FlightRecorder


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper-only meme-coin flight recorder")
    parser.add_argument("--config", default="config/default.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("health")
    show = sub.add_parser("events")
    show.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    settings = load_settings(args.config)
    recorder = FlightRecorder(settings.database_path)
    if args.command == "init-db":
        result = {"database": str(settings.database_path), "created": True}
    elif args.command == "health":
        result = {
            "execution_mode": settings.execution_mode,
            "journal_valid": recorder.verify_chain(),
            "settings": asdict(settings),
        }
    else:
        result = recorder.list_events(args.limit)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
