#!/usr/bin/env bash
# preflight.sh — Constellation environment gate. Exit nonzero = do not proceed.
#
# Adaptations from the Phase 0 spec (probed 2026-07-01):
#   * notes.json schema is {conversation_id: [note, ...]} -> count = sum of list lengths
#   * No code in this repo consumes ANTHROPIC_API_KEY yet (Dream Cycle lands Phase 1),
#     so an empty key is INFO, not FAIL. Flip the marked line to `bad` in Phase 1.
set -uo pipefail
FAIL=0
ok()   { printf "  \033[32mPASS\033[0m %s\n" "$1"; }
bad()  { printf "  \033[31mFAIL\033[0m %s\n      remedy: %s\n" "$1" "$2"; FAIL=1; }
info() { printf "  \033[36mINFO\033[0m %s\n" "$1"; }

# Air-side gate: on clyde-air these are hard requirements (FAIL); elsewhere
# (e.g. the MBP repo source) they stay advisory (INFO). Keyed on LocalHostName,
# which is reliably 'clyde-air' even when the network hostname differs (.lan).
HOST="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"
airgate() { if [[ "$HOST" == "clyde-air" ]]; then bad "$1" "$2"; else info "$1"; fi; }

echo "== Constellation preflight $(date -u +%FT%TZ) on $(hostname) =="

# --- binaries ---
for bin in git jq rsync curl lsof; do
  command -v "$bin" >/dev/null && ok "binary: $bin" || bad "binary: $bin missing" "brew install $bin"
done
command -v cloudflared >/dev/null && ok "binary: cloudflared $(cloudflared --version 2>/dev/null | head -1)" \
  || airgate "cloudflared absent (required on the Air)" "brew install cloudflared"
command -v tailscale >/dev/null && ok "binary: tailscale" \
  || airgate "tailscale absent (required on the Air)" "brew install --cask tailscale, then Tailscale menu -> Install CLI"
# Tunnel gate (hardened 2026-07-02): the symlinked app-bundle CLI emits empty
# `tailscale status`, so CLI chatter is not a reliable liveness signal. Probe the
# actual tailnet path instead -- nc -z to the two source coordinates on ssh/22
# proves the tunnel is up end-to-end, which is what Group 2's pulls depend on.
TS_MBP="100.101.100.104"; TS_IMAC="100.81.255.12"
ts_probe() { nc -z -G 5 "$1" 22 >/dev/null 2>&1; }
ts_probe "$TS_MBP"  && ok "tailnet tunnel: MBP $TS_MBP:22 reachable" \
  || airgate "tailnet tunnel: MBP $TS_MBP:22 unreachable" "confirm tailscale up on both ends (tailscale status)"
ts_probe "$TS_IMAC" && ok "tailnet tunnel: iMac $TS_IMAC:22 reachable" \
  || airgate "tailnet tunnel: iMac $TS_IMAC:22 unreachable" "confirm tailscale up on both ends (tailscale status)"

# --- python / venv ---
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VPY="$REPO/.venv/bin/python"
if [[ -x "$VPY" ]]; then
  PYV="$("$VPY" --version 2>&1)"
  [[ "$PYV" == *"3.12."* ]] && ok "venv python: $PYV" || bad "venv python is $PYV, need 3.12.x" "uv venv --python 3.12 $REPO/.venv && $REPO/.venv/bin/pip install -r $REPO/requirements.txt"
else
  bad ".venv missing at $REPO/.venv" "uv venv --python 3.12 $REPO/.venv && $REPO/.venv/bin/pip install -r $REPO/requirements.txt"
fi
if [[ -x "$VPY" ]]; then
  for mod in numpy sentence_transformers yaml fastmcp pytest; do
    "$VPY" -c "import $mod" 2>/dev/null && ok "import: $mod" || bad "import: $mod" "$REPO/.venv/bin/pip install $mod"
  done
fi

# --- secrets (presence only, never values) ---
if [[ -f "$REPO/.env" ]]; then
  PERM=$(stat -f "%Lp" "$REPO/.env")
  [[ "$PERM" == "600" ]] && ok ".env exists, mode 600" || bad ".env mode is $PERM" "chmod 600 $REPO/.env"
  # presence-only, never echo the value; stdlib grep avoids a python-dotenv dep
  # (not in requirements.txt; deps stay frozen). PHASE1: now a hard gate on the Air.
  if grep -Eq '^[[:space:]]*ANTHROPIC_API_KEY[[:space:]]*=[[:space:]]*.+' "$REPO/.env"; then
    ok "ANTHROPIC_API_KEY present (bool check)"
  else
    airgate "ANTHROPIC_API_KEY empty (required on the Air for Phase 1 Dream Cycle)" "add ANTHROPIC_API_KEY=<key> to $REPO/.env"
  fi
else
  bad ".env missing" "touch $REPO/.env && chmod 600 $REPO/.env (already gitignored)"
fi
grep -q '^\.env$' "$REPO/.gitignore" 2>/dev/null && ok ".env gitignored" || bad ".env not in .gitignore" "echo .env >> $REPO/.gitignore"

# --- git state ---
cd "$REPO"
BR=$(git branch --show-current)
[[ "$BR" == "main" ]] && ok "branch: main" || bad "branch: $BR" "git checkout main"
git remote get-url origin >/dev/null 2>&1 && ok "remote: origin set" || info "no origin remote"
[[ -z "$(git status --porcelain)" ]] && ok "working tree clean" || info "uncommitted changes present: $(git status --porcelain | wc -l | tr -d ' ') files"

# --- data integrity ---
if [[ -f "$REPO/data/notes.json" ]]; then
  NOTES=$("$VPY" -c "import json; d=json.load(open('$REPO/data/notes.json')); print(sum(len(v) for v in d.values()))" 2>/dev/null || echo '?')
  ok "data/notes.json present ($NOTES notes)"
else
  bad "data/notes.json missing" "restore from $REPO/backups/ (most recent notes.json.*)"
fi

echo "== preflight $( [[ $FAIL -eq 0 ]] && echo PASSED || echo FAILED ) =="
exit $FAIL
