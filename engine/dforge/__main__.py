"""Command line: `python -m dforge serve|doctor`."""

from __future__ import annotations

import argparse
import json


def main() -> None:
    parser = argparse.ArgumentParser(prog="dforge")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Run the local engine API")
    serve.add_argument("--port", type=int, default=7863)
    serve.add_argument("--token", default=None)
    serve.add_argument("--parent-pid", type=int, default=None)
    sub.add_parser("doctor", help="Report what is installed")
    args = parser.parse_args()
    if args.command == "serve":
        from .server import serve as run_server

        run_server(args.port, args.token, args.parent_pid)
    elif args.command == "doctor":
        from .config import capabilities

        print(json.dumps(capabilities(), indent=2))


if __name__ == "__main__":
    main()
