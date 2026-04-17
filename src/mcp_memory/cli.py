"""CLI entry point for mcp-memory."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from .config import get_db_path

_DEFAULT_PORT = "8000"


def _find_binary() -> str:
    """Find the mcp-memory binary path."""
    path = shutil.which("mcp-memory")
    if not path:
        print("Error: mcp-memory not found in PATH", file=sys.stderr)
        sys.exit(1)
    return path


def _setup_launchd(binary: str, port: str, db_path: Path) -> None:
    """Generate and install a macOS launchd plist."""
    label = "com.mcp-memory"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    log_path = db_path.parent / "mcp-memory.log"

    plist_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    plist_path.write_text(
        textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>{label}</string>
            <key>ProgramArguments</key>
            <array>
                <string>{binary}</string>
            </array>
            <key>EnvironmentVariables</key>
            <dict>
                <key>MCP_MEMORY_DB_PATH</key>
                <string>{db_path}</string>
                <key>MCP_MEMORY_PORT</key>
                <string>{port}</string>
            </dict>
            <key>RunAtLoad</key>
            <true/>
            <key>KeepAlive</key>
            <true/>
            <key>StandardOutPath</key>
            <string>{log_path}</string>
            <key>StandardErrorPath</key>
            <string>{log_path}</string>
        </dict>
        </plist>
    """)
    )

    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}", str(plist_path)],
        capture_output=True,
        check=False,
    )
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
        check=True,
    )
    subprocess.run(
        ["launchctl", "kickstart", f"gui/{uid}/{label}"],
        check=True,
    )
    print(f"Installed launchd service: {plist_path}")
    print(f"  Logs: {log_path}")


def _setup_systemd(binary: str, port: str, db_path: Path) -> None:
    """Generate and install a Linux systemd user unit."""
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_path = unit_dir / "mcp-memory.service"
    log_path = db_path.parent / "mcp-memory.log"

    unit_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    unit_path.write_text(
        textwrap.dedent(f"""\
        [Unit]
        Description=mcp-memory server
        After=network.target

        [Service]
        ExecStart={binary}
        Environment=MCP_MEMORY_DB_PATH={db_path}
        Environment=MCP_MEMORY_PORT={port}
        Restart=always
        RestartSec=3
        StandardOutput=append:{log_path}
        StandardError=append:{log_path}

        [Install]
        WantedBy=default.target
    """)
    )

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", "mcp-memory.service"],
        check=True,
    )
    print(f"Installed systemd user service: {unit_path}")
    print(f"  Logs: {log_path}")
    print("  Status: systemctl --user status mcp-memory")


def _cmd_setup_service(args: argparse.Namespace) -> None:
    """Handle the setup-service subcommand."""
    binary = _find_binary()
    port = args.port
    db_path = Path(args.db_path).expanduser()

    system = platform.system()
    if system == "Darwin":
        _setup_launchd(binary, port, db_path)
    elif system == "Linux":
        _setup_systemd(binary, port, db_path)
    else:
        print(f"Unsupported platform: {system}", file=sys.stderr)
        sys.exit(1)

    print(f"Server running on http://localhost:{port}/mcp")


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(prog="mcp-memory", description="MCP memory server")
    sub = parser.add_subparsers(dest="command")

    setup = sub.add_parser("setup-service", help="Install as a persistent background service")
    setup.add_argument(
        "--port",
        default=os.environ.get("MCP_MEMORY_PORT", _DEFAULT_PORT),
        help=f"HTTP port (default: {_DEFAULT_PORT}, or MCP_MEMORY_PORT)",
    )
    setup.add_argument(
        "--db-path",
        default=str(get_db_path()),
        help="Database path (default: ~/.local/share/mcp-memory/memory.db, or MCP_MEMORY_DB_PATH)",
    )

    return parser


def main() -> None:
    """CLI entry point: no args = serve, setup-service = install as service."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "setup-service":
        _cmd_setup_service(args)
    else:
        from .server import main as serve

        serve()
