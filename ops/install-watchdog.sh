#!/usr/bin/env bash
set -euo pipefail

# Installs a systemd timer on the VPS to run ops/watchdog.py every 2 minutes.
# Usage: sudo bash ops/install-watchdog.sh [/path/to/oil-tanker-tracker]

REPO_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON_BIN="$(command -v python3)"
STATE_DIR="/var/lib/oil-monitor-watchdog"

if [[ -z "$PYTHON_BIN" ]]; then
  echo "python3 is required" >&2
  exit 1
fi

mkdir -p "$STATE_DIR"

cat >/etc/systemd/system/oil-monitor-watchdog.service <<EOF
[Unit]
Description=Oil Tanker Tracker Host Watchdog
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
WorkingDirectory=${REPO_DIR}
EnvironmentFile=-${REPO_DIR}/.env
Environment=WATCHDOG_COMPOSE_FILE=${REPO_DIR}/docker-compose.prod.yml
Environment=WATCHDOG_STATE_FILE=${STATE_DIR}/state.json
ExecStart=${PYTHON_BIN} ${REPO_DIR}/ops/watchdog.py
EOF

cat >/etc/systemd/system/oil-monitor-watchdog.timer <<EOF
[Unit]
Description=Run Oil Tanker Tracker Host Watchdog every 2 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
AccuracySec=15s

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now oil-monitor-watchdog.timer
systemctl status oil-monitor-watchdog.timer --no-pager
