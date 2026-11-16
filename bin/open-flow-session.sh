#!/usr/bin/env bash
set -euo pipefail

NODE_BIN="${FLOW_NODE_BIN:-/home/gabrielcruzsoto/.nvm/versions/node/v24.22.0/bin/node}"
FLOW_MCP_DIR="${FLOW_MCP_DIR:-/home/gabrielcruzsoto/.local/share/flow-mcp}"

if [[ ! -x "$NODE_BIN" ]]; then
  NODE_BIN="$(command -v node)"
fi

exec "$NODE_BIN" "$FLOW_MCP_DIR/dist/login.js"
