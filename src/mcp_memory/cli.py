"""CLI entry point for mcp-memory."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

from .config import (
    _DEFAULT_PORT,
    detect_service_port,
    get_agent_port,
    get_db_path,
    get_default_db_path,
)
from .relocate import parse_db_path_from_plist, parse_db_path_from_systemd, relocate_db

_LAUNCHD_LABEL = "com.mcp-memory"
_LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"
_SYSTEMD_UNIT = Path("/etc/systemd/system/mcp-memory.service")


@dataclass(frozen=True)
class _ServiceSpec:
    """Everything that differs between the two servers when installing a service."""

    name: str
    binary_name: str
    label: str
    description: str
    port: str
    env: dict[str, str]
    log_path: Path
    plist_path: Path
    systemd_unit: Path


def _memory_spec(port: str, db_path: Path) -> _ServiceSpec:
    """Service spec for the mcp-memory data server."""
    return _ServiceSpec(
        name="memory",
        binary_name="mcp-memory",
        label=_LAUNCHD_LABEL,
        description="mcp-memory server",
        port=port,
        env={"MCP_MEMORY_DB_PATH": str(db_path), "MCP_MEMORY_PORT": port},
        log_path=db_path.parent / "mcp-memory.log",
        plist_path=_LAUNCHD_PLIST,
        systemd_unit=_SYSTEMD_UNIT,
    )


def _agent_spec(port: str) -> _ServiceSpec:
    """Service spec for the memory-agent recall server.

    Bakes the installing user's PATH into the service env: recall spawns the
    `claude` CLI, and launchd/systemd otherwise run with a minimal PATH that
    would not find it.
    """
    label = "com.memory-agent"
    env = {"MCP_AGENT_PORT": port}
    path = os.environ.get("PATH")
    if path:
        env["PATH"] = path
    # Carry any dream-curation settings through so `MCP_DREAM_* memory-agent
    # setup-service` sticks in the installed service.
    env.update({k: v for k, v in os.environ.items() if k.startswith("MCP_DREAM_")})
    return _ServiceSpec(
        name="memory-agent",
        binary_name="memory-agent",
        label=label,
        description="memory-agent recall server",
        port=port,
        env=env,
        log_path=get_default_db_path().parent / "memory-agent.log",
        plist_path=Path.home() / "Library" / "LaunchAgents" / f"{label}.plist",
        systemd_unit=Path("/etc/systemd/system/memory-agent.service"),
    )


def _detect_service_port() -> str:
    """Read the port from an installed service config, falling back to env/default."""
    return detect_service_port() or os.environ.get("MCP_MEMORY_PORT", _DEFAULT_PORT)


def _find_binary(name: str) -> str:
    """Find a console-script binary path, or exit with an error."""
    path = shutil.which(name)
    if not path:
        print(f"Error: {name} not found in PATH", file=sys.stderr)
        sys.exit(1)
    return path


def _render_plist(spec: _ServiceSpec, *, binary: str) -> str:
    """Render a macOS launchd plist for a service spec."""
    env_entries = "\n".join(
        f"                <key>{key}</key>\n                <string>{value}</string>"
        for key, value in spec.env.items()
    )
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>{spec.label}</string>
            <key>ProgramArguments</key>
            <array>
                <string>{binary}</string>
            </array>
            <key>EnvironmentVariables</key>
            <dict>
{env_entries}
            </dict>
            <key>RunAtLoad</key>
            <true/>
            <key>KeepAlive</key>
            <true/>
            <key>StandardOutPath</key>
            <string>{spec.log_path}</string>
            <key>StandardErrorPath</key>
            <string>{spec.log_path}</string>
        </dict>
        </plist>
    """)


def _render_systemd(spec: _ServiceSpec, *, binary: str, user: str) -> str:
    """Render a systemd unit for a service spec."""
    env_lines = "\n".join(f"Environment={key}={value}" for key, value in spec.env.items())
    return (
        "[Unit]\n"
        f"Description={spec.description}\n"
        "After=network.target\n\n"
        "[Service]\n"
        f"ExecStart={binary}\n"
        f"{env_lines}\n"
        f"User={user}\n"
        "Restart=always\n"
        "RestartSec=3\n"
        f"StandardOutput=append:{spec.log_path}\n"
        f"StandardError=append:{spec.log_path}\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _setup_launchd(spec: _ServiceSpec, binary: str) -> None:
    """Generate and install a macOS launchd plist from a service spec."""
    spec.plist_path.parent.mkdir(parents=True, exist_ok=True)
    spec.log_path.parent.mkdir(parents=True, exist_ok=True)
    spec.plist_path.write_text(_render_plist(spec, binary=binary))

    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}", str(spec.plist_path)],
        capture_output=True,
        check=False,
    )
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(spec.plist_path)], check=True)
    subprocess.run(["launchctl", "kickstart", f"gui/{uid}/{spec.label}"], check=True)
    print(f"Installed launchd service: {spec.plist_path}")
    print(f"  Logs: {spec.log_path}")


def _setup_systemd(spec: _ServiceSpec, binary: str) -> None:
    """Generate and install a system-wide systemd unit from a service spec."""
    spec.log_path.parent.mkdir(parents=True, exist_ok=True)
    unit_content = _render_systemd(spec, binary=binary, user=getpass.getuser())

    subprocess.run(
        ["sudo", "tee", str(spec.systemd_unit)],
        input=unit_content.encode(),
        capture_output=True,
        check=True,
    )
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    subprocess.run(["sudo", "systemctl", "enable", "--now", spec.systemd_unit.name], check=True)
    print(f"Installed systemd service: {spec.systemd_unit}")
    print(f"  Logs: {spec.log_path}")
    print(f"  Status: sudo systemctl status {spec.name}")


def _setup_service_from_spec(spec: _ServiceSpec) -> None:
    """Install a service for the given spec on the current platform."""
    binary = _find_binary(spec.binary_name)
    system = platform.system()
    if system == "Darwin":
        _setup_launchd(spec, binary)
    elif system == "Linux":
        _setup_systemd(spec, binary)
    else:
        print(f"Unsupported platform: {system}", file=sys.stderr)
        sys.exit(1)
    print(f"Server running on http://localhost:{spec.port}/mcp")


def _cmd_setup_service(args: argparse.Namespace) -> None:
    """Handle the setup-service subcommand."""
    _setup_service_from_spec(_memory_spec(args.port, Path(args.db_path).expanduser()))


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


def _register_claude_code_server(claude_bin: str, name: str, url: str) -> None:
    """Add one user-scoped HTTP MCP server to Claude Code and allow its tools."""
    result = subprocess.run(
        [claude_bin, "mcp", "get", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        print(f"{name} MCP server already configured in Claude Code.")
    else:
        subprocess.run(
            [claude_bin, "mcp", "add", "--transport", "http", "--scope", "user", name, url],
            check=False,
        )
        print(f"Added {name} MCP server to Claude Code (url: {url}).")

    settings_path = Path.home() / ".claude" / "settings.json"
    settings = (
        json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    )
    permissions: dict[str, list[str]] = settings.setdefault("permissions", {})
    allow: list[str] = permissions.setdefault("allow", [])
    rule = f"mcp__{name}__*"
    if rule not in allow:
        allow.append(rule)
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        print(f"Added {rule} to permissions.allow.")


def _cmd_install_claude_code() -> None:
    """Register both the memory data server and the memory-agent recall server."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        print("error: claude not found on PATH", file=sys.stderr)
        sys.exit(1)

    _register_claude_code_server(
        claude_bin, "memory", f"http://localhost:{_detect_service_port()}/mcp"
    )
    _register_claude_code_server(
        claude_bin, "memory-agent", f"http://localhost:{get_agent_port()}/mcp"
    )


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
