"""CLI entry point for mcp-memory."""

from __future__ import annotations

import argparse
import json
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
    """Generate and install a system-wide systemd unit."""
    import getpass

    unit_path = Path("/etc/systemd/system/mcp-memory.service")
    log_path = db_path.parent / "mcp-memory.log"
    user = getpass.getuser()

    db_path.parent.mkdir(parents=True, exist_ok=True)

    unit_content = textwrap.dedent(f"""\
        [Unit]
        Description=mcp-memory server
        After=network.target

        [Service]
        ExecStart={binary}
        Environment=MCP_MEMORY_DB_PATH={db_path}
        Environment=MCP_MEMORY_PORT={port}
        User={user}
        Restart=always
        RestartSec=3
        StandardOutput=append:{log_path}
        StandardError=append:{log_path}

        [Install]
        WantedBy=multi-user.target
    """)

    subprocess.run(
        ["sudo", "tee", str(unit_path)],
        input=unit_content.encode(),
        capture_output=True,
        check=True,
    )
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    subprocess.run(
        ["sudo", "systemctl", "enable", "--now", "mcp-memory.service"],
        check=True,
    )
    print(f"Installed systemd service: {unit_path}")
    print(f"  Logs: {log_path}")
    print("  Status: sudo systemctl status mcp-memory")


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


def _cmd_install_kiro(args: argparse.Namespace) -> None:
<<<<<<< Updated upstream
    """Patch Kiro MCP config with the memory server entry."""
=======
    """Patch Kiro MCP config and optionally agent config for memory."""
>>>>>>> Stashed changes
    port = args.port
    mcp_config = Path(args.mcp_config).expanduser()

    mcp_config.parent.mkdir(parents=True, exist_ok=True)

    if mcp_config.exists():
        config = json.loads(mcp_config.read_text(encoding="utf-8"))
    else:
        config = {}

    servers = config.setdefault("mcpServers", {})
<<<<<<< Updated upstream
    entry = {"url": f"http://localhost:{port}/mcp"}

    if "memory" in servers:
        print(f"{mcp_config} already has memory server entry.")
        return

    servers["memory"] = entry
    mcp_config.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"Patched {mcp_config} with memory server (port {port}).")
=======

    if "memory" in servers:
        print(f"{mcp_config} already has memory server entry.")
    else:
        servers["memory"] = {"url": f"http://localhost:{port}/mcp"}
        mcp_config.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        print(f"Patched {mcp_config} with memory server (port {port}).")

    agent_config = getattr(args, "agent_config", None)
    if agent_config:
        agent_path = Path(agent_config).expanduser()
        if agent_path.exists():
            agent = json.loads(agent_path.read_text(encoding="utf-8"))
            allowed: list[str] = agent.get("allowedTools", [])
            if "@memory" not in allowed:
                allowed.append("@memory")
                agent["allowedTools"] = allowed
                agent_path.write_text(json.dumps(agent, indent=2) + "\n", encoding="utf-8")
                print(f"Added @memory to allowedTools in {agent_path}.")
            else:
                print(f"{agent_path} already has @memory in allowedTools.")
        else:
            print(f"Agent config {agent_path} not found, skipping allowedTools.", file=sys.stderr)
>>>>>>> Stashed changes


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

    install = sub.add_parser("install", help="Patch agent MCP config with memory server entry")
    install.add_argument("target", choices=["kiro"], help="Agent to install for.")
    install.add_argument(
        "--port",
        default=os.environ.get("MCP_MEMORY_PORT", _DEFAULT_PORT),
        help=f"HTTP port (default: {_DEFAULT_PORT}, or MCP_MEMORY_PORT)",
    )
    install.add_argument(
        "--mcp-config",
        default="~/.kiro/settings/mcp.json",
        help="Path to Kiro MCP config (default: ~/.kiro/settings/mcp.json)",
    )
<<<<<<< Updated upstream
=======
    install.add_argument(
        "--agent-config",
        metavar="PATH",
        help="Kiro agent JSON to patch with @memory in allowedTools.",
    )
>>>>>>> Stashed changes

    return parser


def main() -> None:
    """CLI entry point: no args = serve, setup-service = install as service."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "setup-service":
        _cmd_setup_service(args)
    elif args.command == "install":
        _cmd_install_kiro(args)
    else:
        from .server import main as serve

        serve()
