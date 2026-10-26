"""CLI entry point for mcp-memory."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import getpass
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import subprocess
import sys
import textwrap

from .config import (
    _DEFAULT_PORT,
    get_db_path,
    get_default_db_path,
    get_surfaced_retention_days,
    resolve_port,
)
from .jsonc import load_jsonc_object
from .relocate import parse_db_path_from_plist, parse_db_path_from_systemd, relocate_db

__all__ = ["Path", "argparse", "subprocess"]

_LAUNCHD_LABEL = "com.mcp-memory"
_LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"
_SYSTEMD_UNIT = Path("/etc/systemd/system/mcp-memory.service")
_LEGACY_AGENT_LABEL = "com.memory-agent"
_LEGACY_AGENT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{_LEGACY_AGENT_LABEL}.plist"
_LEGACY_AGENT_UNIT = Path("/etc/systemd/system/memory-agent.service")
_LEGACY_AGENT_SERVERS = ("memory-agent", "memory_agent")
_PLIST_DREAM_ENV_RE = re.compile(r"<key>(MCP_DREAM_\w+)</key>\s*<string>([^<]*)</string>")
_SYSTEMD_DREAM_ENV_RE = re.compile(r"^Environment=(MCP_DREAM_\w+)=(.*)$", re.MULTILINE)
_PLIST_ENV_START_RE = re.compile(r"<key>EnvironmentVariables</key>\s*<dict>\n")


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
    env = {"MCP_MEMORY_DB_PATH": str(db_path), "MCP_MEMORY_PORT": port}
    # Carry any soft-delete purge, GC, and surfaced-retention settings through so
    # `MCP_MEMORY_PURGE_*/MCP_MEMORY_GC_*/MCP_MEMORY_SURFACED_RETENTION_DAYS
    # mcp-memory setup-service` sticks in the installed service.
    env.update({k: v for k, v in os.environ.items() if k.startswith("MCP_MEMORY_PURGE_")})
    env.update({k: v for k, v in os.environ.items() if k.startswith("MCP_MEMORY_GC_")})
    if "MCP_MEMORY_SURFACED_RETENTION_DAYS" in os.environ:
        env["MCP_MEMORY_SURFACED_RETENTION_DAYS"] = os.environ["MCP_MEMORY_SURFACED_RETENTION_DAYS"]
    env.update({k: v for k, v in os.environ.items() if k.startswith("MCP_DREAM_")})
    path = os.environ.get("PATH")
    if path:
        env["PATH"] = path
    return _ServiceSpec(
        name="memory",
        binary_name="mcp-memory",
        label=_LAUNCHD_LABEL,
        description="mcp-memory server",
        port=port,
        env=env,
        log_path=db_path.parent / "mcp-memory.log",
        plist_path=_LAUNCHD_PLIST,
        systemd_unit=_SYSTEMD_UNIT,
    )


def _find_binary(name: str) -> str:
    """Find a console-script binary path, or exit with an error."""
    path = shutil.which(name)
    if not path:
        print(f"Error: {name} not found in PATH", file=sys.stderr)
        sys.exit(1)
    return path


def _plist_env_entries(env: dict[str, str]) -> str:
    """Render env vars as launchd plist EnvironmentVariables entries."""
    return "\n".join(
        f"                <key>{key}</key>\n                <string>{value}</string>" for key, value in env.items()
    )


def _systemd_env_lines(env: dict[str, str]) -> str:
    """Render env vars as systemd Environment= lines."""
    return "\n".join(f"Environment={key}={value}" for key, value in env.items())


def _render_plist(spec: _ServiceSpec, *, binary: str) -> str:
    """Render a macOS launchd plist for a service spec."""
    env_entries = _plist_env_entries(spec.env)
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
    env_lines = _systemd_env_lines(spec.env)
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


def _loaded_launchd_plist(uid: int, label: str) -> str | None:
    """Return the plist path launchd loaded a label from, or None if it is not loaded."""
    result = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    match = re.search(r"^\s*path = (.+)$", result.stdout, re.MULTILINE)
    return match.group(1).strip() if match else None


def _reload_launchd(plist_path: Path, label: str) -> None:
    """Reload a launchd job from its plist and start it."""
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist_path)], capture_output=True, check=False)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], check=True)
    subprocess.run(["launchctl", "kickstart", f"gui/{uid}/{label}"], check=True)


def _setup_launchd(spec: _ServiceSpec, binary: str) -> None:
    """Generate and install a macOS launchd plist from a service spec.

    Refuses when the label is already loaded from a different plist: launchd labels
    are per user, so a plist under another HOME would replace that HOME's service.
    """
    uid = os.getuid()
    loaded = _loaded_launchd_plist(uid, spec.label)
    if loaded and loaded != str(spec.plist_path):
        print(
            f"Error: {spec.label} is already loaded from {loaded}; "
            f"run `launchctl bootout gui/{uid}/{spec.label}` first to replace it",
            file=sys.stderr,
        )
        sys.exit(1)

    spec.plist_path.parent.mkdir(parents=True, exist_ok=True)
    spec.log_path.parent.mkdir(parents=True, exist_ok=True)
    spec.plist_path.write_text(_render_plist(spec, binary=binary))
    _reload_launchd(spec.plist_path, spec.label)
    print(f"Installed launchd service: {spec.plist_path}")
    print(f"  Logs: {spec.log_path}")


def _setup_systemd(spec: _ServiceSpec, binary: str) -> None:
    """Generate and install a system-wide systemd unit from a service spec.

    Refuses to replace an existing unit while HOME differs from the user's real home,
    since the unit path is fixed and the new unit would point at the redirected HOME.
    """
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    if spec.systemd_unit.exists() and Path.home() != real_home:
        print(
            f"Error: {spec.systemd_unit} already exists and HOME is {Path.home()}, not {real_home}; "
            "run with the real HOME to replace it",
            file=sys.stderr,
        )
        sys.exit(1)

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


def _dream_env_files(system: str) -> tuple[Path, Path, re.Pattern[str]] | None:
    """Return the retired-agent file, the mcp-memory service file and the MCP_DREAM_* pattern for a platform."""
    if system == "Darwin":
        return _LEGACY_AGENT_PLIST, _LAUNCHD_PLIST, _PLIST_DREAM_ENV_RE
    if system == "Linux":
        return _LEGACY_AGENT_UNIT, _SYSTEMD_UNIT, _SYSTEMD_DREAM_ENV_RE
    return None


def _read_dream_env(path: Path, pattern: re.Pattern[str]) -> dict[str, str]:
    """Read the MCP_DREAM_* env from a service file, or {} when it is absent."""
    return dict(pattern.findall(path.read_text(encoding="utf-8"))) if path.exists() else {}


def _keep_installed_dream_env(spec: _ServiceSpec) -> _ServiceSpec:
    """Keep MCP_DREAM_* already in the installed service unless the spec sets them."""
    files = _dream_env_files(platform.system())
    if not files:
        return spec
    env = dict(spec.env)
    for key, value in _read_dream_env(files[1], files[2]).items():
        env.setdefault(key, value)
    return replace(spec, env=env)


def _migrate_legacy_dream_env() -> None:
    """Copy MCP_DREAM_* from the retired memory-agent service into the installed mcp-memory service.

    Values already set on the mcp-memory service are kept. Reloads it only when something was added.
    """
    system = platform.system()
    files = _dream_env_files(system)
    if not files:
        return
    legacy, main, pattern = files
    legacy_env = _read_dream_env(legacy, pattern)
    if not legacy_env:
        return
    if not main.exists():
        print(
            f"Warning: no mcp-memory service for the retired {', '.join(legacy_env)}; set them, run setup-service",
            file=sys.stderr,
        )
        return
    content = main.read_text(encoding="utf-8")
    current = dict(pattern.findall(content))
    missing = {key: value for key, value in legacy_env.items() if key not in current}
    if not missing:
        return
    if system == "Darwin":
        main.write_text(
            _PLIST_ENV_START_RE.sub(lambda m: f"{m.group(0)}{_plist_env_entries(missing)}\n", content, count=1)
        )
        _reload_launchd(main, _LAUNCHD_LABEL)
    else:
        updated = content.replace("[Service]\n", f"[Service]\n{_systemd_env_lines(missing)}\n", 1)
        subprocess.run(["sudo", "tee", str(main)], input=updated.encode(), capture_output=True, check=True)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        subprocess.run(["sudo", "systemctl", "restart", main.name], check=True)
    print(f"Moved {', '.join(missing)} from the retired memory-agent service to {main}")


def _remove_legacy_agent_service() -> None:
    """Stop and delete the retired memory-agent service, if one is installed."""
    _migrate_legacy_dream_env()
    system = platform.system()
    if system == "Darwin" and _LEGACY_AGENT_PLIST.exists():
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}", str(_LEGACY_AGENT_PLIST)],
            capture_output=True,
            check=False,
        )
        _LEGACY_AGENT_PLIST.unlink()
        print(f"Removed retired memory-agent launchd service: {_LEGACY_AGENT_PLIST}")
    elif system == "Linux" and _LEGACY_AGENT_UNIT.exists():
        subprocess.run(["sudo", "systemctl", "disable", "--now", _LEGACY_AGENT_UNIT.name], check=False)
        subprocess.run(["sudo", "rm", str(_LEGACY_AGENT_UNIT)], check=True)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        print(f"Removed retired memory-agent systemd service: {_LEGACY_AGENT_UNIT}")


def _cmd_setup_service(args: argparse.Namespace) -> None:
    """Handle the setup-service subcommand."""
    _setup_service_from_spec(_keep_installed_dream_env(_memory_spec(args.port, Path(args.db_path).expanduser())))
    _remove_legacy_agent_service()


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


def _cmd_restart() -> None:
    """Restart the running mcp-memory service, or report that none is installed."""
    system = platform.system()
    if system == "Darwin" and _LAUNCHD_PLIST.exists():
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{_LAUNCHD_LABEL}"],
            check=False,
        )
    elif system == "Linux" and _SYSTEMD_UNIT.exists():
        result = subprocess.run(["sudo", "systemctl", "restart", _SYSTEMD_UNIT.name], check=False)
    else:
        print("No mcp-memory service installed on this platform.")
        return

    if result.returncode != 0:
        print("Error: failed to restart mcp-memory service.", file=sys.stderr)
        sys.exit(result.returncode)
    print("Restarted mcp-memory service.")


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
        port = resolve_port()
        _cmd_setup_service(argparse.Namespace(port=port, db_path=str(target)))
        print("Service repointed at the default database location.")
    else:
        print("No installed service found; start the server to use the migrated database.")


def _cmd_audit(args: argparse.Namespace) -> None:
    """Emit the read-only structural-hygiene report, or a proposed fix plan, as JSON on stdout."""
    from .audit import audit_graph, propose_plan
    from .storage import open_readonly

    db_path = get_db_path()
    if not db_path.exists():
        print(f"Error: database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    db = open_readonly(db_path)
    try:
        report = audit_graph(db, None if args.all_projects else args.project)
        plan = propose_plan(db, report) if args.propose_plan else None
    finally:
        db.connection.close()
    if plan is not None:
        print(json.dumps({"steps": plan}, indent=2))
    else:
        print(json.dumps(report, indent=2))


def _cmd_eval(args: argparse.Namespace) -> None:
    """Report search-ranking quality over recorded retrieval telemetry (read-only)."""
    from .eval import evaluate
    from .storage import open_readonly

    db_path = get_db_path()
    if not db_path.exists():
        print(f"Error: database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    db = open_readonly(db_path)
    try:
        report = evaluate(db, args.k, since=args.since, min_content_tokens=args.min_content_tokens)
    finally:
        db.connection.close()
    window = f", since {args.since}" if args.since else ""
    if args.min_content_tokens:
        window += f", min_content_tokens={args.min_content_tokens}"
    print(f"Ranking quality over {report.query_count} labelled queries (k={report.k}{window}):")
    print(f"  mean precision@{report.k}: {report.mean_precision_at_k:.3f}")
    print(
        f"    fraction of top-{report.k} results that were relevant; best case up to 1.0 "
        f"({1 / report.k:.3g} if only 1 relevant item), worst case 0.0"
    )
    print(f"  MRR: {report.mrr:.3f}")
    mrr_bounds = "best case 1.0, worst case 0.0"
    print(
        f"    1/rank of the first relevant result; {mrr_bounds}; "
        f"{report.mrr:.3f} means it sits around rank {1 / report.mrr:.1f}"
        if report.mrr
        else f"    1/rank of the first relevant result; {mrr_bounds}"
    )
    print(f"  mean recall@{report.k}: {report.mean_recall_at_k:.3f}")
    print(f"    fraction of all relevant items that made it into the top {report.k}; best case 1.0, worst case 0.0")
    print(f"  mean nDCG@{report.k}: {report.mean_ndcg_at_k:.3f}")
    print(
        "    ranking quality vs. the ideal order, normalised to the true relevant-set size; "
        "best case 1.0, worst case 0.0; not capped by small relevant sets like precision is"
    )
    print(f"  mean success@{report.k}: {report.mean_success_at_k:.3f}")
    print(
        f"    whether any relevant item made it into the top {report.k}; best case 1.0, "
        "worst case 0.0; not subject to the precision@k ceiling that a single-relevant-item "
        "query imposes on precision@k"
    )


def _cmd_metrics(args: argparse.Namespace) -> None:
    """Emit per-tool usage metrics (byte-size proxies + option frequencies) as JSON on stdout."""
    from dataclasses import asdict

    from .metrics import usage_report
    from .storage import open_writable

    db = open_writable(get_db_path())
    try:
        report = usage_report(db, since=args.since)
    finally:
        db.connection.close()
    print(json.dumps(asdict(report), indent=2))


def _cmd_export(args: argparse.Namespace) -> None:
    """Export the entire memory database to a JSON file."""
    from .export_import import export_database
    from .storage import open_writable

    output_path = Path(args.output_path).expanduser()
    db = open_writable(get_db_path())
    try:
        export_database(db, output_path)
    finally:
        db.connection.close()
    print(f"Exported database to {output_path}")


def _cmd_import(args: argparse.Namespace) -> None:
    """Merge selected projects from a JSON export into the local database."""
    from .export_import import import_projects, list_export_projects, load_export
    from .storage import open_writable

    try:
        data = load_export(Path(args.input_path).expanduser())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not args.project:
        print("Projects available in export file:")
        for name in list_export_projects(data):
            print(f"  {name}")
        print("Re-run with --project name1,name2 to import (nothing was imported).")
        return

    names = [name.strip() for name in args.project.split(",") if name.strip()]
    db = open_writable(get_db_path())
    try:
        summary = import_projects(db, data, names, dry_run=args.dry_run)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.connection.close()
    print(summary.render())


def _remove_legacy_agent_servers(mcp_path: Path, servers_key: str) -> bool:
    """Delete the retired memory-agent entries from an MCP config file, leaving all else intact."""
    if not mcp_path.exists() or mcp_path.stat().st_size == 0:
        return False
    config = load_jsonc_object(mcp_path)
    servers = config.get(servers_key)
    if not isinstance(servers, dict):
        return False
    stale = [name for name in _LEGACY_AGENT_SERVERS if name in servers]
    for name in stale:
        del servers[name]
    if stale:
        mcp_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        print(f"Removed retired {', '.join(stale)} MCP server from {mcp_path}.")
    return bool(stale)


def _cmd_install_kiro(args: argparse.Namespace) -> None:
    """Patch Kiro agent config with memory MCP server and allowedTools."""
    port = args.port
    agent_path = Path(args.agent_config).expanduser()

    if not agent_path.exists():
        print(f"error: {agent_path} does not exist", file=sys.stderr)
        sys.exit(1)

    agent = json.loads(agent_path.read_text(encoding="utf-8"))
    changed = False

    removed = [name for name in _LEGACY_AGENT_SERVERS if name in agent.get("mcpServers", {})]
    for name in removed:
        del agent["mcpServers"][name]
    stale_tools = {f"@{name}" for name in _LEGACY_AGENT_SERVERS}
    for key in ("allowedTools", "tools"):
        if stale_tools & set(agent.get(key, [])):
            agent[key] = [tool for tool in agent[key] if tool not in stale_tools]
            removed.append(key)
    if removed:
        changed = True
        print(f"Removed retired memory-agent entries ({', '.join(removed)}) from {agent_path}.")

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
    settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    permissions: dict[str, list[str]] = settings.setdefault("permissions", {})
    allow: list[str] = permissions.setdefault("allow", [])
    rule = f"mcp__{name}__*"
    if rule not in allow:
        allow.append(rule)
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        print(f"Added {rule} to permissions.allow.")


def _remove_legacy_claude_code_servers(claude_bin: str) -> None:
    """Remove the retired memory-agent servers and their allow rules from Claude Code."""
    for name in _LEGACY_AGENT_SERVERS:
        registered = subprocess.run([claude_bin, "mcp", "get", name], capture_output=True, check=False)
        if registered.returncode == 0:
            subprocess.run([claude_bin, "mcp", "remove", "--scope", "user", name], check=False)
            print(f"Removed retired {name} MCP server from Claude Code.")

    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    allow = settings.get("permissions", {}).get("allow", [])
    stale = {f"mcp__{name}__*" for name in _LEGACY_AGENT_SERVERS}
    if stale & set(allow):
        settings["permissions"]["allow"] = [rule for rule in allow if rule not in stale]
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        print(f"Removed retired memory-agent rules from permissions.allow in {settings_path}.")


def _cmd_install_claude_code() -> None:
    """Register the memory data server in Claude Code."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        print("error: claude not found on PATH", file=sys.stderr)
        sys.exit(1)

    _remove_legacy_claude_code_servers(claude_bin)
    _register_claude_code_server(claude_bin, "memory", f"http://localhost:{resolve_port()}/mcp")


def _register_codex_server(codex_bin: str, name: str, url: str) -> None:
    """Add one HTTP MCP server to Codex if it is not already registered."""
    result = subprocess.run(
        [codex_bin, "mcp", "get", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        print(f"{name} MCP server already configured in Codex.")
    else:
        subprocess.run([codex_bin, "mcp", "add", name, "--url", url], check=False)
        print(f"Added {name} MCP server to Codex (url: {url}).")


def _remove_legacy_codex_servers(codex_bin: str) -> None:
    """Remove the retired memory-agent servers from Codex."""
    for name in _LEGACY_AGENT_SERVERS:
        registered = subprocess.run([codex_bin, "mcp", "get", name], capture_output=True, check=False)
        if registered.returncode == 0:
            subprocess.run([codex_bin, "mcp", "remove", name], check=False)
            print(f"Removed retired {name} MCP server from Codex.")


def _cmd_install_codex() -> None:
    """Register the memory data server in Codex."""
    codex_bin = shutil.which("codex")
    if not codex_bin:
        print("error: codex not found on PATH", file=sys.stderr)
        sys.exit(1)

    _remove_legacy_codex_servers(codex_bin)
    _register_codex_server(codex_bin, "memory", f"http://127.0.0.1:{resolve_port()}/mcp")


def _register_antigravity_server(agy_bin: str, name: str, url: str) -> None:
    """Add one HTTP MCP server to Antigravity if it is not already registered."""
    result = subprocess.run(
        [agy_bin, "mcp", "list"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and name in result.stdout:
        print(f"{name} MCP server already configured in Antigravity.")
    else:
        subprocess.run([agy_bin, "mcp", "add", name, url], check=False)
        print(f"Added {name} MCP server to Antigravity (url: {url}).")


def _cmd_install_antigravity() -> None:
    """Register the memory data server in Antigravity."""
    agy_bin = shutil.which("agy")
    url = f"http://localhost:{resolve_port()}/mcp"
    mcp_path = Path.home() / ".gemini" / "config" / "mcp_config.json"
    _remove_legacy_agent_servers(mcp_path, "mcpServers")
    if agy_bin:
        _register_antigravity_server(agy_bin, "memory", url)
    else:
        mcp_path.parent.mkdir(parents=True, exist_ok=True)
        config: dict[str, object] = {}
        if mcp_path.exists() and mcp_path.stat().st_size > 0:
            try:
                config = json.loads(mcp_path.read_text(encoding="utf-8"))
            except Exception:
                config = {}
        servers_obj = config.setdefault("mcpServers", {})
        if isinstance(servers_obj, dict):
            servers_obj["memory"] = {"serverUrl": url, "disabled": False}
        mcp_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        print(f"Added memory MCP server to Antigravity config {mcp_path}")


def _default_copilot_mcp_config_path() -> Path:
    """Return the default VS Code MCP config path for this environment."""
    home = Path.home()
    if platform.system() == "Darwin":
        local_candidates = [home / "Library" / "Application Support" / "Code" / "User" / "mcp.json"]
    else:
        local_candidates = []
        if os.environ.get("WSL_DISTRO_NAME"):
            local_candidates += [
                home / ".vscode-server" / "data" / "User" / "mcp.json",
            ]
        if xdg_config_home := os.environ.get("XDG_CONFIG_HOME"):
            local_candidates.append(Path(xdg_config_home) / "Code" / "User" / "mcp.json")
        local_candidates.append(home / ".config" / "Code" / "User" / "mcp.json")

    for candidate in local_candidates:
        if candidate.exists():
            return candidate

    if os.environ.get("WSL_DISTRO_NAME"):
        win_user = os.environ.get("USERNAME") or os.environ.get("USER")
        if win_user:
            candidate = Path("/mnt/c/Users") / win_user / "AppData/Roaming/Code/User/mcp.json"
            if candidate.exists():
                return candidate
        matches = sorted(Path("/mnt/c/Users").glob("*/AppData/Roaming/Code/User/mcp.json"))
        if matches:
            return matches[0]
    return local_candidates[0]


def _register_copilot_server(mcp_path: Path, name: str, url: str) -> None:
    """Add one HTTP MCP server entry to VS Code's mcp.json if missing."""
    if mcp_path.exists() and mcp_path.stat().st_size > 0:
        config = load_jsonc_object(mcp_path)
    else:
        config = {}

    servers_obj = config.setdefault("servers", {})
    if not isinstance(servers_obj, dict):
        msg = f"error: {mcp_path} has non-object 'servers'"
        raise TypeError(msg)

    servers: dict[str, object] = servers_obj
    if name in servers:
        print(f"{name} MCP server already configured in VS Code Copilot.")
        return

    servers[name] = {"type": "http", "url": url}
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"Added {name} MCP server to VS Code Copilot (url: {url}).")


def _cmd_install_copilot(args: argparse.Namespace) -> None:
    """Register memory servers in VS Code Copilot MCP config."""
    mcp_path = Path(args.mcp_config).expanduser() if args.mcp_config else _default_copilot_mcp_config_path()
    try:
        _remove_legacy_agent_servers(mcp_path, "servers")
        _register_copilot_server(mcp_path, "memory", f"http://localhost:{resolve_port()}/mcp")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: failed to update {mcp_path}: {exc}", file=sys.stderr)
        sys.exit(1)


def _pi_mcp_config_path() -> Path:
    """Return pi's global MCP config, under `$PI_CODING_AGENT_DIR` or ~/.pi/agent."""
    agent_dir = os.environ.get("PI_CODING_AGENT_DIR")
    return (Path(agent_dir).expanduser() if agent_dir else Path.home() / ".pi" / "agent") / "mcp.json"


def _register_pi_server(mcp_path: Path, name: str, url: str) -> None:
    """Add one HTTP MCP server entry to pi's mcp.json if missing.

    An existing entry keeps its settings; only the legacy `directTools` and
    `toolPrefix` keys are dropped.
    """
    config: dict[str, object] = load_jsonc_object(mcp_path) if mcp_path.exists() and mcp_path.stat().st_size > 0 else {}
    servers_obj = config.setdefault("mcpServers", {})
    if not isinstance(servers_obj, dict):
        msg = f"error: {mcp_path} has non-object 'mcpServers'"
        raise TypeError(msg)

    servers: dict[str, object] = servers_obj
    existing = servers.get(name)
    if existing is not None:
        if isinstance(existing, dict) and ({"directTools", "toolPrefix"} & existing.keys()):
            existing.pop("directTools", None)
            existing.pop("toolPrefix", None)
            mcp_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        print(f"{name} MCP server already configured in Pi.")
        return

    servers[name] = {"url": url}
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"Added {name} MCP server to Pi (url: {url}).")


def _cmd_install_pi() -> None:
    """Register memory servers in pi's global MCP config."""
    mcp_path = _pi_mcp_config_path()
    try:
        _remove_legacy_agent_servers(mcp_path, "mcpServers")
        _register_pi_server(mcp_path, "memory", f"http://localhost:{resolve_port()}/mcp")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: failed to update {mcp_path}: {exc}", file=sys.stderr)
        sys.exit(1)


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(prog="mcp-memory", description="MCP memory server")
    sub = parser.add_subparsers(dest="command")

    setup = sub.add_parser("setup-service", help="Install as a persistent background service")
    setup.add_argument(
        "--port",
        default=resolve_port(),
        help=f"HTTP port (default: MCP_MEMORY_PORT, else the installed service's port, else {_DEFAULT_PORT})",
    )
    setup.add_argument(
        "--db-path",
        default=str(get_db_path()),
        help="Database path (default: ~/.local/share/mcp-memory/memory.db, or MCP_MEMORY_DB_PATH)",
    )

    sub.add_parser("restart", help="Restart the running background service")

    migrate = sub.add_parser(
        "migrate-db",
        help="Move the database to the default location and repoint the service",
    )
    migrate.add_argument(
        "--source",
        default=None,
        help="Source database path (default: auto-detected from the installed service)",
    )

    audit = sub.add_parser("audit", help="Report structural memory-graph hygiene issues as JSON (read-only)")
    scope = audit.add_mutually_exclusive_group(required=True)
    scope.add_argument("--project", help="Audit a single project scope")
    scope.add_argument("--all-projects", action="store_true", help="Audit every project scope")
    audit.add_argument(
        "--propose-plan",
        action="store_true",
        help="Emit a structured plan of tool calls to fix findings, instead of the raw report",
    )

    evaluate_cmd = sub.add_parser(
        "eval",
        help="Report search-ranking quality over recorded retrieval telemetry (read-only)",
    )
    evaluate_cmd.add_argument(
        "--k",
        type=int,
        default=10,
        help="Rank cutoff for precision@k (default: 10)",
    )
    retention_days = get_surfaced_retention_days()
    retention_note = (
        "telemetry is never pruned, so this reaches back to the oldest recorded retrieval"
        if retention_days < 0
        else f"telemetry is pruned after ~{retention_days} days, so this cannot reach further back"
    )
    evaluate_cmd.add_argument(
        "--since",
        default=None,
        help=(
            "Only score retrievals surfaced on or after this point (relative "
            f"'30m'/'1h'/'7d'/'2w'/'3mo' or ISO date); {retention_note}"
        ),
    )
    evaluate_cmd.add_argument(
        "--min-content-tokens",
        type=int,
        default=0,
        help=(
            "Exclude queries with fewer whitespace-separated tokens than this (e.g. the "
            "single word 'task') from the labelled set (default: 0, no filtering)"
        ),
    )

    metrics_cmd = sub.add_parser(
        "metrics", help="Report per-tool usage metrics (byte-size proxies and option frequency)."
    )
    metrics_cmd.add_argument(
        "--since",
        default=None,
        help=("Only include calls on or after this instant (relative '30m'/'1h'/'7d'/'2w'/'3mo' or ISO date)."),
    )

    export_cmd = sub.add_parser("export", help="Export the entire memory database to a JSON file")
    export_cmd.add_argument("output_path")

    import_cmd = sub.add_parser("import", help="Merge selected projects from a JSON export into the local database")
    import_cmd.add_argument("input_path")
    import_cmd.add_argument(
        "--project",
        default=None,
        help="Comma-separated project names to import (omit to list available projects)",
    )
    import_cmd.add_argument("--dry-run", action="store_true")

    install = sub.add_parser("install", help="Patch agent config with memory MCP server")
    install.add_argument(
        "target",
        choices=["kiro", "claude-code", "codex", "copilot", "antigravity", "pi"],
        help="Agent to install for.",
    )
    install.add_argument(
        "agent_config",
        nargs="?",
        help="Path to the Kiro agent JSON file to patch (required for kiro).",
    )
    install.add_argument(
        "--port",
        default=resolve_port(),
        help=f"HTTP port (default: MCP_MEMORY_PORT, else the installed service's port, else {_DEFAULT_PORT})",
    )
    install.add_argument(
        "--mcp-config",
        default=None,
        help=(
            "Path to VS Code mcp.json for the copilot target "
            "(default: current-OS VS Code user config path (Linux/XDG/macOS); "
            "else WSL Windows fallback under /mnt/c/Users)"
        ),
    )

    return parser


def _cmd_install(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Dispatch the `install` subcommand to its target-specific handler."""
    _remove_legacy_agent_service()
    if args.target == "claude-code":
        _cmd_install_claude_code()
    elif args.target == "codex":
        _cmd_install_codex()
    elif args.target == "copilot":
        _cmd_install_copilot(args)
    elif args.target == "antigravity":
        _cmd_install_antigravity()
    elif args.target == "pi":
        _cmd_install_pi()
    else:
        if not args.agent_config:
            parser.error("agent_config is required for kiro")
        _cmd_install_kiro(args)


def main() -> None:
    """CLI entry point: no args = serve, setup-service = install as service."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "setup-service":
        _cmd_setup_service(args)
    elif args.command == "restart":
        _cmd_restart()
    elif args.command == "migrate-db":
        _cmd_migrate_db(args)
    elif args.command == "audit":
        _cmd_audit(args)
    elif args.command == "eval":
        _cmd_eval(args)
    elif args.command == "metrics":
        _cmd_metrics(args)
    elif args.command == "export":
        _cmd_export(args)
    elif args.command == "import":
        _cmd_import(args)
    elif args.command == "install":
        _cmd_install(args, parser)
    else:
        from .server import main as serve

        serve()
