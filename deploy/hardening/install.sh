#!/bin/bash
# Constellation tunnel hardening installer — run with sudo on clyde-air.
# 1) live config -> ~/.cloudflared/config.yml   2) root-owned preflight launcher
# 3) repointed LaunchDaemon + logs out of repo  4) verify tunnel returns
# 5) only then restore repo template + remove stale .bak
# Automatic rollback to previous daemon on verification failure.
set -euo pipefail

STAGE="/Users/ama/tunnel-hardening"
BACKUP="$STAGE/backup-$(date +%Y%m%d-%H%M%S)"
PLIST_DST="/Library/LaunchDaemons/com.constellation.tunnel.plist"
WRAP_DST="/usr/local/bin/constellation-tunnel-launch"
CFG_DST="/Users/ama/.cloudflared/config.yml"
REPO="/Users/ama/dev/constellation-v3"
LOGDIR="/Users/ama/Library/Logs/constellation"

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
say() { printf '\n== %s\n' "$*"; }

say "0/7 backup current state -> $BACKUP"
mkdir -p "$BACKUP"
cp "$PLIST_DST" "$BACKUP/com.constellation.tunnel.plist.orig"
cp "$REPO/deploy/cloudflared-config.yml" "$BACKUP/cloudflared-config.yml.live"

say "1/7 install live config -> $CFG_DST (ama, 600)"
install -m 600 -o ama -g staff "$STAGE/config.yml" "$CFG_DST"

say "2/7 install preflight launcher -> $WRAP_DST (root, 755)"
install -d -o root -g wheel -m 755 /usr/local/bin
install -m 755 -o root -g wheel "$STAGE/constellation-tunnel-launch" "$WRAP_DST"

say "3/7 log dir -> $LOGDIR"
mkdir -p "$LOGDIR"; chown ama:staff "$LOGDIR"

say "4/7 preflight --check as ama (validate only, no launch)"
sudo -u ama "$WRAP_DST" --check

say "5/7 swap daemon plist + restart (brief public blip ~10s)"
install -m 644 -o root -g wheel "$STAGE/com.constellation.tunnel.plist" "$PLIST_DST"
launchctl bootout system/com.constellation.tunnel 2>/dev/null || true
sleep 2
pkill -f cloudflared 2>/dev/null || true
sleep 1
launchctl bootstrap system "$PLIST_DST"

say "6/7 verify new tunnel (up to 30s)"
ok=""
for i in $(seq 1 15); do
  sleep 2
  if pgrep -f 'cloudflared.*\.cloudflared/config\.yml.*run' >/dev/null \
     && curl -sf --max-time 3 http://127.0.0.1:20241/ready >/dev/null; then
    ok=1; break
  fi
done
if [ -z "$ok" ]; then
  echo "!! verification FAILED — rolling back to previous daemon"
  install -m 644 -o root -g wheel "$BACKUP/com.constellation.tunnel.plist.orig" "$PLIST_DST"
  launchctl bootout system/com.constellation.tunnel 2>/dev/null || true
  sleep 2
  launchctl bootstrap system "$PLIST_DST"
  echo "!! rollback complete — previous daemon active. Staging kept for diagnosis."
  exit 1
fi
echo "tunnel process + metrics /ready confirmed:"
pgrep -fl cloudflared
tail -n 3 "$LOGDIR/tunnel.preflight.log" 2>/dev/null || true

say "7/7 disarm repo landmine (daemon no longer reads repo path)"
sudo -u ama git -C "$REPO" checkout -- deploy/cloudflared-config.yml
rm -f "$REPO/deploy/cloudflared-config.yml.bak"
echo "deploy/ git state now:"
sudo -u ama git -C "$REPO" status --porcelain deploy/ || true

say "DONE — live config: $CFG_DST | launcher: $WRAP_DST | logs: $LOGDIR"
