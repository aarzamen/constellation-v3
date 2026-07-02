#!/usr/bin/env bash
# mcp_health.sh — Constellation MCP server health gate (Phase 0, Group 2).
# Thin wrapper: all logic lives in scripts/mcp_health.py so the pytest suite
# (tests/test_mcp_health.py) exercises the identical code paths.
# Usage: scripts/mcp_health.sh [--skip-network]
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec "$REPO/.venv/bin/python" "$REPO/scripts/mcp_health.py" "$@"
