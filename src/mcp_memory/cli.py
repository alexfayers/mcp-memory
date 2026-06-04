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

from .config import get_db_path, get_default_db_path
from .relocate import parse_db_path_from_plist, parse_db_path_from_systemd, relocate_db

_DEFAULT_PORT = "8000"
_LAUNCHD_LABEL = "com.mcp-memory"
_LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"
_SYSTEMD_UNIT = Path("/etc/systemd/system/mcp-memory.service")


def _detect_service_port() -> str:
    """Read the port from an installed service config, falling back to default."""
    if _LAUNCHD_PLIST.exists():
        import re

        content = _LAUNCHD_PLIST.read_text(encoding="utf-8")
        match = re.search(r"<key>MCP_MEMORY_PORT</key>\s*<string>(\d+)</string>", content)
        if match:
            return match.group(1)

    if _SYSTEMD_UNIT.exists():
        for line in _SYSTEMD_UNIT.read_text(encoding="utf-8").splitlines():
            if line.startswith("Environment=MCP_MEMORY_PORT="):
                return line.split("=", 2)[2]

    return os.environ.get("MCP_MEMORY_PORT", _DEFAULT_PORT)


def _find_binary() -> str:
    """Find the mcp-memory binary path."""
    path = shutil.which("mcp-memory")
    if not path:
        print("Error: mcp-memory not found in PATH", file=sys.stderr)
        sys.exit(1)
    return path


def _setup_launchd(binary: str, port: str, db_path: Path) -> None:
    """Generate and install a macOS launchd plist."""
    label = _LAUNCHD_LABEL
    plist_path = _LAUNCHD_PLIST
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


def _detect_service_db_path() -> str | None:
    """Read the configured DB path from an installed service config, if any."""
    if _LAUNCHD_PLIST.exists():
        return parse_db_path_from_plist(_LAUNCHD_PLIST.read_text(encoding="utf-8"))
    if _SYSTEMD_UNIT.exists():
        return parse_db_path_from_systemd(_SYSTEMD_UNIT.read_text(encoding="utf-8"))
    return None


def _stop_service() -> bool:
    """Stop the running service if installed. Returns True if a service was found."""
    system = platform.system()
    if system == "Darwin" and _LAUNCHD_PLIST.exists():
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}", str(_LAUNCHD_PLIST)],
            capture_output=True,
            check=False,
        )
        return True
    if system == "Linux" and _SYSTEMD_UNIT.exists():
        subprocess.run(["sudo", "systemctl", "stop", "mcp-memory.service"], check=False)
        return True
    return False


def _cmd_migrate_db(args: argparse.Namespace) -> None:
    """Move the database to the default location and repoint the service at it."""
    target = get_default_db_path()
    source = Path(args.source).expanduser() if args.source else None
    if source is None:
        detected = _detect_service_db_path()
        source = Path(detected).expanduser() if detected else get_db_path()

    source = source.resolve()
    if source == target.resolve():
        print(f"Database already at the default location: {target}")
        return
    if not source.exists():
        print(f"Error: source database not found: {source}", file=sys.stderr)
        sys.exit(1)

    had_service = _stop_service()
    try:
        moved = relocate_db(source, target)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Moved {moved} entities: {source} -> {target}")

    if had_service:
        port = _detect_service_port()
        _cmd_setup_service(argparse.Namespace(port=port, db_path=str(target)))
        print("Service repointed at the default database location.")
    else:
        print("No installed service found; start the server to use the migrated database.")


def _cmd_install_kiro(args: argparse.Namespace) -> None:
    """Patch Kiro agent config with memory MCP server and allowedTools."""
    port = args.port
    agent_path = Path(args.agent_config).expanduser()

    if not agent_path.exists():
        print(f"error: {agent_path} does not exist", file=sys.stderr)
        sys.exit(1)

    agent = json.loads(agent_path.read_text(encoding="utf-8"))
    changed = False

    # Check agent config and global mcp.json for existing memory entry.
    agent_servers: dict[str, object] = agent.get("mcpServers", {})
    global_mcp = Path.home() / ".kiro" / "settings" / "mcp.json"
    global_has_memory = False
    if global_mcp.exists():
        global_config = json.loads(global_mcp.read_text(encoding="utf-8"))
        global_has_memory = "memory" in global_config.get("mcpServers", {})

    if "memory" not in agent_servers and not global_has_memory:
        agent_servers["memory"] = {"url": f"http://localhost:{port}/mcp"}
        agent["mcpServers"] = agent_servers
        changed = True
        print(f"Added memory MCP server (port {port}) to {agent_path}.")
    else:
        print(f"{agent_path} already has memory MCP server.")

    allowed: list[str] = agent.get("allowedTools", [])
    if "@memory" not in allowed:
        allowed.append("@memory")
        agent["allowedTools"] = allowed
        changed = True
        print(f"Added @memory to allowedTools in {agent_path}.")
    else:
        print(f"{agent_path} already has @memory in allowedTools.")

    tools: list[str] = agent.get("tools", [])
    if tools != ["*"] and "@memory" not in tools:
        tools.append("@memory")
        agent["tools"] = tools
        changed = True
        print(f"Added @memory to tools in {agent_path}.")

    if changed:
        agent_path.write_text(json.dumps(agent, indent=2) + "\n", encoding="utf-8")


def _cmd_install_claude_code() -> None:
    """Add mcp-memory as a user-scoped MCP server in Claude Code."""
    import shutil
    import subprocess

    claude_bin = shutil.which("claude")
    if not claude_bin:
        print("error: claude not found on PATH", file=sys.stderr)
        sys.exit(1)

    result = subprocess.run(
        [claude_bin, "mcp", "get", "memory"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        print("memory MCP server already configured in Claude Code.")
    else:
        port = _detect_service_port()
        url = f"http://localhost:{port}/mcp"

        subprocess.run(
            [
                claude_bin,
                "mcp",
                "add",
                "--transport",
                "http",
                "--scope",
                "user",
                "memory",
                url,
            ],
            check=False,
        )
        print(f"Added mcp-memory MCP server to Claude Code (url: {url}).")

    # Auto-allow all memory MCP tools
    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    else:
        settings = {}
    permissions: dict[str, list[str]] = settings.setdefault("permissions", {})
    allow: list[str] = permissions.setdefault("allow", [])
    rule = "mcp__memory__*"
    if rule not in allow:
        allow.append(rule)
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        print("Added mcp__memory__* to permissions.allow.")


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

    migrate = sub.add_parser(
        "migrate-db",
        help="Move the database to the default location and repoint the service",
    )
    migrate.add_argument(
        "--source",
        default=None,
        help="Source database path (default: auto-detected from the installed service)",
    )

    install = sub.add_parser("install", help="Patch agent config with memory MCP server")
    install.add_argument("target", choices=["kiro", "claude-code"], help="Agent to install for.")
    install.add_argument(
        "agent_config",
        nargs="?",
        help="Path to the Kiro agent JSON file to patch (required for kiro).",
    )
    install.add_argument(
        "--port",
        default=_detect_service_port(),
        help=f"HTTP port (default: auto-detected from service, or {_DEFAULT_PORT})",
    )

    return parser


def main() -> None:
    """CLI entry point: no args = serve, setup-service = install as service."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "setup-service":
        _cmd_setup_service(args)
    elif args.command == "migrate-db":
        _cmd_migrate_db(args)
    elif args.command == "install":
        if args.target == "claude-code":
            _cmd_install_claude_code()
        else:
            if not args.agent_config:
                parser.error("agent_config is required for kiro")
            _cmd_install_kiro(args)
    else:
        from .server import main as serve

        serve()
