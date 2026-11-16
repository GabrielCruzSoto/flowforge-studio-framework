#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "[flowforge-doctor] Workspace root: $ROOT_DIR"

/usr/bin/python3 -c "import yaml, json; print('[flowforge-doctor] Python3 + PyYAML: OK')"
ffmpeg -version | head -n 1 | sed 's/^/[flowforge-doctor] /'
ffprobe -version | head -n 1 | sed 's/^/[flowforge-doctor] /'

if [[ -f "/home/gabrielcruzsoto/.local/share/flow-mcp/dist/index.js" ]]; then
  echo "[flowforge-doctor] google-flow MCP bridge: OK (/home/gabrielcruzsoto/.local/share/flow-mcp/dist/index.js)"
else
  echo "[flowforge-doctor] WARNING: google-flow MCP bridge not found at ~/.local/share/flow-mcp/dist/index.js"
fi

if command -v google-chrome >/dev/null 2>&1; then
  echo "[flowforge-doctor] Google Chrome binary: $(command -v google-chrome)"
else
  echo "[flowforge-doctor] WARNING: google-chrome not found in PATH"
fi
