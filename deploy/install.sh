#!/usr/bin/env bash
# deploy/install.sh — install the Constellation system LaunchDaemons (Group 3).
#
# HUMAN WALL: this needs sudo. It is NOT run by the night-shift agent. Run it in
# the morning AFTER the Cloudflare tunnel + Access app exist (see MANUAL_STEPS.md)
# and AFTER filling the ACCESS_TEAM_DOMAIN / ACCESS_AUD placeholders in
# deploy/com.constellation.mcp.plist.
#
# Idempotent: boots out any existing instance before bootstrapping the new one.
set -euo pipefail

REPO="/Users/ama/dev/constellation-v3"
DEST="/Library/LaunchDaemons"
PLISTS=(com.constellation.mcp.plist com.constellation.tunnel.plist)

echo "== Constellation daemon install =="

# 0. Refuse to install the MCP plist while the Access placeholders are unfilled.
if grep -q 'REPLACE_ME' "$REPO/deploy/com.constellation.mcp.plist"; then
  echo "ERROR: fill CONSTELLATION_ACCESS_TEAM_DOMAIN / _AUD in" \
       "deploy/com.constellation.mcp.plist first (Cloudflare Access app AUD)." >&2
  exit 1
fi

# 1. Lint before touching the system.
for p in "${PLISTS[@]}"; do
  plutil -lint "$REPO/deploy/$p"
done

# 2. Copy into place, root-owned, 0644 (world-readable — no secrets in these).
for p in "${PLISTS[@]}"; do
  sudo cp "$REPO/deploy/$p" "$DEST/$p"
  sudo chown root:wheel "$DEST/$p"
  sudo chmod 644 "$DEST/$p"
done

# 3. (Re)bootstrap into the system domain.
for p in "${PLISTS[@]}"; do
  label="${p%.plist}"
  sudo launchctl bootout system "$DEST/$p" 2>/dev/null || true
  sudo launchctl bootstrap system "$DEST/$p"
  sudo launchctl enable "system/$label"
  sudo launchctl kickstart -k "system/$label"
done

echo
echo "Installed. Verify:"
echo "  sudo launchctl print system/com.constellation.mcp   | grep -E 'state|pid'"
echo "  sudo launchctl print system/com.constellation.tunnel | grep -E 'state|pid'"
echo "  curl -s http://127.0.0.1:8000/health"
echo "  tail -f $REPO/data/logs/mcp.daemon.err.log"
echo
echo "Uninstall:"
echo "  sudo launchctl bootout system/com.constellation.mcp"
echo "  sudo launchctl bootout system/com.constellation.tunnel"
echo "  sudo rm $DEST/com.constellation.mcp.plist $DEST/com.constellation.tunnel.plist"
