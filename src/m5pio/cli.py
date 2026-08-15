from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_APP = "hello-display"


def available_apps() -> list[str]:
    apps_dir = REPO_ROOT / "apps"
    return sorted(path.name for path in apps_dir.iterdir() if path.is_dir())


def require_app(app: str) -> None:
    if app not in available_apps():
        choices = ", ".join(available_apps()) or "(none)"
        raise SystemExit(f"Unknown app: {app}\nAvailable apps: {choices}")


def run(command: list[str]) -> int:
    environment = os.environ.copy()
    environment["PLATFORMIO_CORE_DIR"] = str(REPO_ROOT / ".cache" / "platformio")

    try:
        completed = subprocess.run(command, cwd=REPO_ROOT, env=environment)
    except KeyboardInterrupt:
        return 130
    return completed.returncode


def platformio(*arguments: str) -> int:
    return run([sys.executable, "-m", "platformio", *arguments])


def add_app_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "app",
        nargs="?",
        default=DEFAULT_APP,
        help=f"PlatformIO environment name (default: {DEFAULT_APP})",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build, upload, and monitor M5StickS3 sample applications."
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    setup_parser = subparsers.add_parser(
        "setup", help="Restore locked dependencies and install app packages."
    )
    add_app_argument(setup_parser)

    build_command = subparsers.add_parser("build", help="Build an application.")
    add_app_argument(build_command)

    upload_parser = subparsers.add_parser(
        "upload", help="Build and upload an application."
    )
    add_app_argument(upload_parser)
    upload_parser.add_argument("--port", help="Serial port, for example COM3.")

    monitor_parser = subparsers.add_parser(
        "monitor", help="Open the serial monitor."
    )
    add_app_argument(monitor_parser)
    monitor_parser.add_argument("--port", help="Serial port, for example COM3.")

    clean_parser = subparsers.add_parser("clean", help="Remove build products.")
    add_app_argument(clean_parser)

    subparsers.add_parser("devices", help="List detected serial devices.")
    subparsers.add_parser("apps", help="List available applications.")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.action == "apps":
        print(*available_apps(), sep="\n")
        return 0

    if args.action == "devices":
        return platformio("device", "list")

    require_app(args.app)

    if args.action == "setup":
        sync_result = run(["uv", "sync", "--frozen"])
        if sync_result != 0:
            return sync_result
        return platformio("pkg", "install", "--environment", args.app)

    if args.action == "build":
        return platformio("run", "--environment", args.app)

    if args.action == "upload":
        command = ["run", "--environment", args.app, "--target", "upload"]
        if args.port:
            command.extend(["--upload-port", args.port])
        return platformio(*command)

    if args.action == "monitor":
        command = ["device", "monitor", "--environment", args.app]
        if args.port:
            command.extend(["--port", args.port])
        return platformio(*command)

    if args.action == "clean":
        return platformio("run", "--environment", args.app, "--target", "clean")

    raise AssertionError(f"Unhandled action: {args.action}")
