# MANUAL_STEPS.md

Human-required steps accumulated during Phase 0. Each entry: `### <short title>` / exact commands in a fenced block / one-line justification. Empty at sprint end = ideal outcome.

### Activate the Group 3 hooks (session restart)

```bash
cd /Users/ama/constellation-v3
claude   # then type /hooks and confirm the 5 hook entries are listed and approved
```

Hooks in `.claude/settings.json` are snapshotted at session start, so the session that installed them cannot live-fire them itself; every rule is instead proven by `tests/test_hooks.py` (44 synthetic stdin cases). On first restart, verify live: `python --version` must be blocked with the venv remedy, and ending a turn with uncommitted changes must be blocked by the Stop hook.

### Append the Phase 0 closeout GRAVITY note to the dev thread (remote write)

In any Claude session with the Constellation connector, say: *"Add this note to conversation 3363bc73-af8f-45c2-a34e-13fed9ea53af"* with the text below — or POST it via the tunnel API. The agent was blocked from doing this because the dev thread exists only on the iMac's serving instance, which Phase 0 was forbidden to touch.

```
[2026-07-01] GRAVITY: Phase 0 completed on the MBP repo (constellation-v3, be3576f..HEAD).
(1) Mar-29 re-embed data-loss bug FIXED + regression-locked — pipeline snapshots/backs up
notes.json before rebuild, merges by note_id after (make merge-safe gates the MERGE_SAFE
flag that unlocks --reembed). (2) Timestamp bug fixed — server/api.py read m['created_at']
instead of m['timestamp']; parsers were already correct since v4.4. Note f56c3e65 RESOLVED.
(3) Guardrail hooks installed repo-wide. (4) Widmark note face018c SURVIVES INTACT in the
MBP's local notes.json — the loss was on this instance's copy only; the Phase 1 dataset
merge restores it here. Local counts: 13 baseline, 13 final. Hailo note migration deferred
to Phase 1 (see MANUAL_STEPS.md in repo).
```

One line each of why: a persistent write to the publicly-served instance crossed the sprint's "do not touch the iMac" boundary (enforced by the permission classifier).

**DONE 2026-07-01** — posted from claude.ai via the Constellation connector, note_id `605d41be`; serving-instance dev thread now carries 16 notes.

### Migrate the 5 hailo-switcher notes off the dev thread (Phase 1, on the iMac dataset)

Notes `ae94e974`, `2532cf08`, `a16b4ebf`, `fafb880d`, `ec8b95dc` live only on the iMac's `data/notes.json` (dev thread `3363bc73-af8f-45c2-a34e-13fed9ea53af`). Migration = add copies to the target conversation, verify readable, then delete the originals — all writes on the iMac instance, forbidden in Phase 0. Additionally, **no confident target exists**: remote search for the hailo-switcher spec session returned only tangential candidates, best two being `68037ea9-0558-8008-bcfe-d16a0fa1ad3a` ("RPI 5 Hailo NPU Setup", ChatGPT 2025-04-19) and `820c3834-e692-49da-9373-1004838a9cdf` ("Multi-OS setup for RPi 5", Claude 2026-02-11) — both notes_count 0, neither is the spec-writing session (it may never have been ingested; it was likely a Claude Code session). Recommend deciding the target (or ingesting the hailo-switcher Claude Code sessions) during Phase 1's merge, then moving the notes there.

**DONE 2026-07-02 (Group 2b)** — the spec session surfaced in the post-C index: `20ab0ef2-578b-4787-b4a4-7120cd824b58` "Debugging front end library on Raspberry Pi" (Claude, **2026-03-28** — inside the late-March gap Dataset C filled; 218×"hailo", 111×"hailo-switcher", 197×"spec", explicit refs to `HAILO-SWITCHER-SPEC.md` and `github.com/aarzamen/hailo-switcher`). It was a claude.ai chat, not a standalone Claude Code JSONL — which is why earlier `~/.claude/projects` searches missed it and only C's fresh export contained it. All 5 notes migrated on the **Air's local canonical sidecar** (`data/notes.json`), preserving original `note_id`/`text`/`created_at`; verified readable on the target; originals removed from the dev thread (now 11 notes, 0 hailo). Total unchanged at 45. **Not applied to the live iMac serving instance** — that is a separate authorized remote write if the legacy instance needs to match.

---

## Phase 1 (clyde-air) — human-required

### Install Tailscale on the Air (Group 0 gap — blocks Group 2 Dataset-B pull)

Tailscale was in the Group 0 bootstrap list but is not installed on clyde-air: no `tailscale`
CLI on PATH and no `/Applications/Tailscale.app`. The tightened preflight now FAILs on this.
Group 2 pulls Dataset B from the MBP over the tailnet, so this gates the merge.

```bash
brew install --cask tailscale
open -a Tailscale            # sign in when the app opens (Mike's tailnet)
# then expose the CLI: Tailscale menu-bar icon -> "Install CLI…"  (adds /usr/local/bin/tailscale)
tailscale status            # confirm clyde-air is on the tailnet; note the MBP's tailnet name/IP
```

Justification: Homebrew cask install + GUI OAuth sign-in cannot be done unattended by the agent.

### Supply ANTHROPIC_API_KEY to the Air's .env (Group 1 step 1)

The agent must never handle the secret value. Mike creates the file; preflight verifies presence
only (bool check, never echoes the value).

```bash
cd /Users/ama/dev/constellation-v3
umask 077 && printf 'ANTHROPIC_API_KEY=%s\n' 'sk-ant-...' > .env   # paste the real key
chmod 600 .env
grep -c '^ANTHROPIC_API_KEY=' .env    # expect 1; do NOT print the value
```

Justification: credential provisioning; `.env` is gitignored and the agent is barred from
writing or echoing secrets. After this + Tailscale, the only remaining preflight FAIL is
`data/notes.json`, which Group 2's union merge resolves.

---

## Phase 1 Group 3 — morning activation runbook (serve + secure)

The night shift built and staged everything that needs no sudo / browser / other
machine (units 0–4, all committed + tested, 181/181). The steps below are the
human walls, **in dependency order**. Note: the kickoff listed "install daemons"
first, but the daemons need the tunnel UUID (tunnel plist) and the Access AUD
(mcp plist) to exist first — and `deploy/install.sh` refuses to run while the
`REPLACE_ME` placeholders remain — so tunnel + Access come before install.

### 1. cloudflared: login, create tunnel, route DNS  (browser OAuth — Mike)

```bash
cloudflared tunnel login                        # browser: authorize the constellation-memory.com zone
cloudflared tunnel create constellation-air     # prints the tunnel UUID; writes ~/.cloudflared/<UUID>.json
cloudflared tunnel route dns constellation-air mcp.constellation-memory.com
cloudflared tunnel list                         # confirm constellation-air; note the UUID
```

### 2. Cloudflare Access application for the hostname  (Zero Trust dashboard — Mike)

- Zero Trust → Access → Applications → Add → **Self-hosted**.
- Application domain: `mcp.constellation-memory.com`
- Policy: **Allow**, include emails → `mikearzamendi@gmail.com` (allowlist).
- Save, then copy from the app Overview: the **Application Audience (AUD) tag**
  and your **team domain** (`https://<team>.cloudflareaccess.com`).
- **claude.ai / claude.com callback caveat:** the claude.ai web MCP connector's
  OAuth callback has a known interop issue completing Cloudflare Access's login
  loop. iOS and Claude Code (which do a full browser handoff) are the primary
  matrix. If the web connector can't complete Access login, use the
  **FastMCP-OAuth fallback** (below) or attach a Cloudflare Access **service
  token** (`CF-Access-Client-Id` / `CF-Access-Client-Secret`) to the connector.

### 3. Fill the staged placeholders

```bash
cd /Users/ama/dev/constellation-v3
UUID='<paste tunnel UUID>'
sed -i '' "s/REPLACE_ME_TUNNEL_UUID/$UUID/g" deploy/cloudflared-config.yml
# edit deploy/com.constellation.mcp.plist:
#   CONSTELLATION_ACCESS_TEAM_DOMAIN -> https://<team>.cloudflareaccess.com
#   CONSTELLATION_ACCESS_AUD         -> <AUD tag>
```

### 4. Install the daemons  (sudo — Mike)

```bash
bash deploy/install.sh          # lints, sudo cp -> /Library/LaunchDaemons, bootstrap+enable+kickstart
curl -s http://127.0.0.1:8000/health                       # {"status":"ok",...,"access_configured":true}
sudo launchctl print system/com.constellation.mcp    | grep -E 'state|pid'
sudo launchctl print system/com.constellation.tunnel | grep -E 'state|pid'
tail -f data/logs/mcp.daemon.err.log data/logs/tunnel.daemon.err.log
```

### 5. Device test matrix  (proceed only if ALL pass)

Public URL is `https://mcp.constellation-memory.com/mcp`.
- **Negative:** `curl -s -o /dev/null -w '%{http_code}\n' https://mcp.constellation-memory.com/mcp` → expect **403** (no Access cookie).
- **claude.ai web:** add custom connector → complete Access login → tools list → run `search_conversations`.
- **iOS Claude app:** same connector → Access login → one tool call.
- **Claude Code:** `claude mcp add --transport http constellation https://mcp.constellation-memory.com/mcp` → Access login → `get_stats`.

### 6. Retire the iMac serving instance  (ONLY after the matrix passes)

The night shift was forbidden to touch the iMac, so first inspect how its
instance is launched (LaunchAgent / helper / manual), then stop it and leave it
**dormant, untouched, 30 days**:

```bash
# on imac@100.81.255.12 — verify the mechanism first, then e.g.:
launchctl bootout gui/$(id -u)/<constellation-label>    # or stop the helper process
```

### FastMCP-OAuth fallback (if the Access web callback bug bites)

If the claude.ai web connector can't complete Cloudflare Access login, serve MCP
auth via FastMCP's own OAuth/bearer provider in front of the tools (in addition
to Access at the edge for iOS/Code), or issue a Cloudflare Access **service
token** and configure the web connector headers. The origin gate
(`CONSTELLATION_REQUIRE_ACCESS=1`) still validates `Cf-Access-Jwt-Assertion`, so
Access remains the edge control for the clients that can complete it.

### HELD — filled deploy config awaiting publish decision (Mike)

The 2026-07-02 clyde-air migration filled real Cloudflare identifiers (named-tunnel
UUID + credentials-file path in `deploy/cloudflared-config.yml`; Access team domain
+ AUD in `deploy/com.constellation.mcp.plist`) into the staged deploy configs. The
agent did **not** commit them: the repo is **public** and, though they are
validation identifiers rather than access-granting secrets (the tunnel credential
JSON is not in the repo), the stop-and-show tripwire applies. The filled versions
are preserved in `git stash@{0}` (working tree holds the `REPLACE_ME` template).
Values are intentionally NOT reproduced here — this file is tracked on public main.

Justification: publishing security identifiers to a public repo is irreversible
(permanent git history) and is Mike's call.

```bash
# To PUBLISH to public main (Mike's explicit OK):
git stash pop                                   # restores the filled deploy files
git add deploy/cloudflared-config.yml deploy/com.constellation.mcp.plist
git commit -m "config: clyde-air migration deploy values (2026-07-02)"
git push origin main

# To KEEP PRIVATE instead: drop the stash and gitignore the filled configs
git stash drop                                  # discard the filled versions from the working tree
printf 'deploy/cloudflared-config.yml\ndeploy/com.constellation.mcp.plist\n' >> .gitignore
# (then re-fill the two files locally from your notes / the tunnel + Access app)
```

---

## Phase 2 — deferred ingest candidates

### RPi5 / Claude Code JSONL ingest (new provider parser)
No `claude-code` JSONL parser exists yet; the 20 existing `claude-code` convs came in
pre-shaped as `local_<uuid>`. A general parser for `~/.claude/projects/**/<session>.jsonl`
(types: user/assistant/summary/ai-title/…) would let RPi5 and other Claude Code sessions be
ingested directly. Not required for the hailo-switcher notes (that spec was a claude.ai chat,
migrated in Group 2b), but useful for future coding-session memory.

### data_fresh/ export extras (Dataset C)
`data_fresh/` also holds `design_chats/` (19), `projects/` (31), `memories.json`, `users.json` —
Phase 2/4 ingest candidates. Only `conversations.json` was parsed in Group 2b.

### Google AI Studio — download the Drive folder (Mike)

The canonical AI Studio chats are `.prompt` files in Mike's Drive folder
"Google AI Studio" (created 2025-03). They are NOT on this Air: no
Drive-for-desktop mount at `~/Library/CloudStorage/GoogleDrive-*`, and the Desktop
"Gemini Apps" Takeout only has a 322-entry `Developers/MyActivity.html` fragment.
To ingest them, get the `.prompt` files onto the Air:

```bash
# In a browser (Mike): drive.google.com -> folder "Google AI Studio"
#   -> right-click -> Download (Drive zips the folder) -> save to ~/Downloads.
# Then, on the Air:
mkdir -p /Users/ama/dev/constellation-v3/ground-truth/google/2026-07-04/ai_studio
unzip ~/Downloads/'Google AI Studio*.zip' \
  -d /Users/ama/dev/constellation-v3/ground-truth/google/2026-07-04/ai_studio
# core/gemini_parser.py already targets AI Studio's chunkedPrompt format and is
# the likely parser (verify against the actual .prompt schema first).
```

Justification: Drive download needs Mike's browser auth; the agent cannot reach
Drive. Full inventory + parser design in `docs/google_ingest_phase0.md`.
