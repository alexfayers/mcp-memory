#!/usr/bin/env bash
# Setup mcp-memory as a persistent service (macOS launchd or Linux systemd).
set -euo pipefail

PORT="${MCP_MEMORY_PORT:-8000}"
DB_PATH="${MCP_MEMORY_DB_PATH:-$HOME/.local/share/mcp-memory/memory.db}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        --db-path) DB_PATH="$2"; shift 2 ;;
        --help)
            echo "Usage: setup-service.sh [--port PORT] [--db-path PATH]"
            echo "  --port     HTTP port (default: 8000, or MCP_MEMORY_PORT)"
            echo "  --db-path  Database path (default: ~/.local/share/mcp-memory/memory.db, or MCP_MEMORY_DB_PATH)"
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

BIN="$(command -v mcp-memory 2>/dev/null || true)"
if [[ -z "$BIN" ]]; then
    echo "Error: mcp-memory not found in PATH. Install with: uv tool install 'git+https://github.com/alexfayers/mcp-memory.git'"
    exit 1
fi

mkdir -p "$(dirname "$DB_PATH")"

setup_launchd() {
    local label="com.mcp-memory"
    local plist="$HOME/Library/LaunchAgents/${label}.plist"
    local log_dir
    log_dir="$(dirname "$DB_PATH")"

    cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${BIN}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>MCP_MEMORY_DB_PATH</key>
        <string>${DB_PATH}</string>
        <key>MCP_MEMORY_PORT</key>
        <string>${PORT}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${log_dir}/mcp-memory.log</string>
    <key>StandardErrorPath</key>
    <string>${log_dir}/mcp-memory.log</string>
</dict>
</plist>
EOF

    launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist"
    echo "Installed launchd service: $plist"
    echo "  Logs: ${log_dir}/mcp-memory.log"
}

setup_systemd() {
    local unit_dir="$HOME/.config/systemd/user"
    local unit="$unit_dir/mcp-memory.service"
    local log_dir
    log_dir="$(dirname "$DB_PATH")"
    mkdir -p "$unit_dir"

    cat > "$unit" <<EOF
[Unit]
Description=mcp-memory server
After=network.target

[Service]
ExecStart=${BIN}
Environment=MCP_MEMORY_DB_PATH=${DB_PATH}
Environment=MCP_MEMORY_PORT=${PORT}
Restart=always
RestartSec=3
StandardOutput=append:${log_dir}/mcp-memory.log
StandardError=append:${log_dir}/mcp-memory.log

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable --now mcp-memory.service
    echo "Installed systemd user service: $unit"
    echo "  Logs: ${log_dir}/mcp-memory.log"
    echo "  Status: systemctl --user status mcp-memory"
}

case "$(uname -s)" in
    Darwin) setup_launchd ;;
    Linux)  setup_systemd ;;
    *)      echo "Unsupported platform: $(uname -s)"; exit 1 ;;
esac

echo "Server running on http://localhost:${PORT}/mcp"
