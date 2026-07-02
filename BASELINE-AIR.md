# BASELINE-AIR.md — Phase 1 Environment Snapshot (clyde-air)

Captured: 2026-07-01 local / 2026-07-02T03:49Z, commit `f3acba8`, branch `main`.
Machine: **clyde-air** (the new sole canonical 24/7 host). Fresh clone from GitHub;
local `main` level with `origin/main` (MBP push confirmed, tree not stale).

## Machine

| Field | Value |
|---|---|
| ComputerName / LocalHostName | `clyde-air` |
| Network hostname | `MacBookAir.lan` (note: `hostname` ≠ LocalHostName; Air-gates key on `scutil --get LocalHostName`) |
| Model | MacBook Air (M1) |
| Arch | arm64 (Apple Silicon) |
| RAM | 8 GB |
| macOS | 26.3.1 (build 25D2128) |
| Disk free | 120 GiB free of 228 GiB (9% used) at capture |

## Toolchain (Group 0 bootstrap)

| Tool | Version | Notes |
|---|---|---|
| git | 2.55.0 | |
| jq | 1.8.2 | |
| rsync | 3.4.4 (protocol 32) | for read-only pulls from iMac / MBP |
| uv | 0.11.26 (Homebrew) | manages the 3.12 interpreter |
| cloudflared | 2026.6.1 | |
| claude (Claude Code) | 2.1.198 | |
| system python3 | 3.9.6 | NOT used; venv is 3.12 |
| **tailscale** | **MISSING** | Group 0 gap — no CLI, no `/Applications/Tailscale.app`. Blocks Group 2 Dataset-B pull over tailnet. → MANUAL_STEPS |

## Python environment

- venv: `/Users/ama/dev/constellation-v3/.venv`, **CPython 3.12.13** (uv-managed).
- Creating the venv **arms the Phase 0 enforcement hooks** — `guard_bash`, `guard_files`,
  `post_bash`, `stop_discipline` all invoke `.venv/bin/python`, which did not exist on the
  fresh clone. Before this step the hooks were inert.

### Resolved dependency versions (pinned in venv)

| Package | Version |
|---|---|
| numpy | 2.5.0 |
| pyyaml | 6.0.3 |
| sentence-transformers | 5.6.0 |
| fastmcp | 3.4.2 |
| torch | 2.12.1 |
| scikit-learn | 1.9.0 |
| transformers | 5.12.1 |
| tokenizers | 0.22.2 |
| pytest | 9.1.1 (dev/test tool; not a frozen runtime dep) |

Deviations from `requirements.txt` (5 lines: numpy, sentence-transformers, pyyaml, fastmcp,
customtkinter):
- **pytest** installed separately — required by `preflight.sh` (`import pytest`) and the test
  suite, but it is a test tool, not a frozen runtime dependency.
- **customtkinter** installed per requirements, but the GUI helper is not used on this headless
  server host.
- **python-dotenv is NOT installed** and NOT added. `preflight.sh` previously imported it for the
  API-key bool check; that check was rewritten to a stdlib `grep` (see below) to keep deps frozen.

## Embedding model

- Model: `all-MiniLM-L6-v2`, 384 dimensions (confirmed by a live encode).
- Cache path: `/Users/ama/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2`
- On-disk size: ~87 MB. First cold load: ~12.3 s.

## Test suite

Full run (`.venv/bin/python -m pytest tests/`): **158 passed, 8 skipped** (166 collected), 16.8 s.

- All **44 hook tests** (`tests/test_hooks.py`) pass.
- The 8 skips are all `"No pre-computed data available"` — the `TestSearchEngine` / `TestEndToEnd`
  cases that require a populated `data/` directory. On a fresh clone `data/` does not exist yet;
  **Group 2's dataset union merge creates it**, at which point the full 166/166 becomes reachable.
- `make merge-safe`: notes-merge regression suite **10/10 pass** → `.claude/flags/MERGE_SAFE`
  present (machine-local, gitignored). `--reembed` is now unlocked on this clone per guard_bash Rule 5.

## preflight.sh — Phase 1 tightening (Group 1 step 2)

Edits made (keyed on `clyde-air` via `scutil --get LocalHostName`, so the MBP stays advisory):
- `cloudflared` / `tailscale` absent → **FAIL** on the Air (was INFO).
- `ANTHROPIC_API_KEY` empty → **FAIL** on the Air (flipped the `# PHASE1: make this bad()` marker).
- API-key check rewritten from a `python-dotenv` heredoc to a stdlib presence-`grep`
  (`grep -Eq '^\s*ANTHROPIC_API_KEY\s*=\s*.+'`) — presence-only, never echoes the value,
  and removes an undeclared dependency.

## preflight.sh — current status on clyde-air (exit 1)

Remaining FAILs, all expected at this stage:

| Check | Status | Resolved by |
|---|---|---|
| tailscale absent | FAIL | Group 0 gap → install + GUI sign-in (human) |
| `.env` missing | FAIL | Mike supplies `ANTHROPIC_API_KEY` → `.env` (chmod 600) |
| `data/notes.json` missing | FAIL | Group 2 dataset union merge |

Everything else PASSes (binaries incl. cloudflared, venv 3.12, all imports, branch/remote/tree).

> Note: the `data/notes.json` FAIL remedy still points at `backups/`, which does not exist on a
> fresh clone. That remedy is written for the steady-state install; here the honest resolution is
> Group 2, not a restore. Left as-is to avoid weakening the steady-state gate.

## Open Decisions (Group 0 defaults, not overridden in kickoff)

1. **FileVault: ON** (default) — accepted cost: physical login after power loss before services start.
2. **Access identity: email OTP** to Mike's Gmail (default).
3. Public-release user account — deferred to Phase 4.

## Group 2b — Dataset C union merge (2026-07-01/02)

Fresh Anthropic export ("Dataset C") folded into the corpus, before Group 3.

### Dataset C provenance
- Source: `~/Downloads/data-ba821840-04bd-4972-ad33-04d8704d6c4a-1782971800-066306e0-batch-0000/`
- Staged to `./data_fresh/` (gitignored) so the pipeline never depends on `~/Downloads`.
- **SHA-256 `data_fresh/conversations.json`:** `7ca7f3b28607106bdbb61ceb244e56b79dc09183dee48fd497175f00f7db5820` (303,767,439 bytes / 290 MB).
- Parse: 290 MB `json.load` peaked at **1.23 GB RSS** (well under the 8 GB ceiling; no streaming needed). Parser yields **1278 conversations / 18,557 messages** (raw export lists 1,341 convs; 63 dropped as text-empty — expected `parser.py:163` behavior). Newest timestamp **2026-07-02T05:54:21Z**.

### Union rule (C authoritative for provider=claude)
- Union = **all of C** (1278 claude, authoritative on id-collision) **+ A/B convs whose id ∉ C** (archival grace for deleted-since-export chats).
- A (the prior iMac union, 1782) contributed **743 preserved** (chatgpt 723 + claude-code 20); all **1039** A-claude ids were superseded by C. **0** A-claude were absent from C (nothing deleted since export).
- On-disk A convs lack `user_messages` (stripped on save); reconstructed from user-role `messages` for embedding, matching the parser's shape.

### First real merge-safe re-embed (`force_reembed=True`, MERGE_SAFE earned)
- **2021 conversations**, 14,820 user-message embeddings, 10 clusters, 9637 edges. Wall clock **149 s** (M1/8GB, all-MiniLM-L6-v2, local — **no API spend**; there is no `search_text`/synthesis stage in this codebase).
- `conversations.json` + `embeddings.npy` mtimes moved **Mar 21 → 2026-07-01 23:24**.

### get_stats (post-merge)
```json
{"totalConversations": 2021, "totalMessages": 31130,
 "dateRange": ["2023-12-13", "2026-07-02"],
 "embeddingModel": "all-MiniLM-L6-v2", "embeddingDim": 384,
 "providers": {"claude": 1278, "claude-code": 20, "chatgpt": 723}}
```

### Assertions (all pass)
- conversations 2021 ≥ 2000 ✓ · max date 2026-07-02 ≥ 2026-07-01 ✓ · total messages 31,130 > old 27,095 (substantially) ✓
- note count 45 ≥ 45 ✓ · four canary note IDs (`face018c`, `f56c3e65`, `06298723`, `605d41be`) + all 5 hailo present ✓
- **notes.json byte-identical pre/post re-embed** (SHA-256 `fcdf717e34704ec6c11d1530d99336c858d793f790945f000a180aa236f4a0ea`) ✓
- consistency: emb rows == chunk_map == graph nodes; dim 384; no orphan note conv_ids ✓
- test suite 166/166 ✓

### Deferred to Phase 2/4 — recorded, NOT parsed tonight
`data_fresh/` also contains: `design_chats/` (19 entries), `projects/` (31 entries), `memories.json` (95,064 B), `users.json` (170 B). These are Phase 2/4 ingest candidates; the Anthropic conversation parser was run on `conversations.json` only.

### Before/After (official get_stats — A∪B vs A∪B∪C)

| Metric | Before (A∪B, iMac) | After (A∪B∪C, this Air) |
|---|---|---|
| Conversations | 1782 | **2021** (+239) |
| Messages | 27,095 | **31,130** (+4,035) |
| Date range | 2023-12-13 → 2026-03-20 | 2023-12-13 → **2026-07-02** |
| Providers | claude 1039 / claude-code 20 / chatgpt 723 | claude 1278 / claude-code 20 / chatgpt 723 |

Embedding model unchanged: all-MiniLM-L6-v2 (384d).

### RESOLVED — hailo-switcher 5-note migration (original Group 2 step 7)
The first search (Group 2b) found **no** hailo-switcher spec session under `~/.claude/projects/` on either machine, because it was never a standalone Claude Code JSONL. Re-running the search against the **post-C index** surfaced it immediately — Dataset C filled the late-March gap where it lives:
- **Target: `20ab0ef2-578b-4787-b4a4-7120cd824b58`** — "Debugging front end library on Raspberry Pi" (Claude, **2026-03-28**), a claude.ai chat where Mike acted as interface to Claude Code. Content is unambiguous: 218×"hailo", 111×"hailo-switcher", 197×"spec", 71×"tauri", with explicit `HAILO-SWITCHER-SPEC.md` / `github.com/aarzamen/hailo-switcher` references. The migrated notes themselves name this "hailo-switcher spec writing session."
- All 5 notes (`ae94e974`, `2532cf08`, `a16b4ebf`, `fafb880d`, `ec8b95dc`) moved on the **Air's local canonical sidecar**, preserving original `note_id`/`text`/`created_at`; verified readable on the target. Dev thread `3363bc73` dropped 16 → **11 notes, 0 hailo**. Total unchanged at **45** (23 → 24 conv keys). Verbatim note text printed to the session log before deletion.
- **Not applied to the live iMac serving instance** — a separate authorized remote write if the legacy instance needs to match.

Note: the RPi5 Claude Code JSONL ingest as a general capability remains a Phase 2 item (see MANUAL_STEPS.md) — but it is **not** required for this migration, since the spec conversation came in via the claude.ai export.

### Sanitization posture
Tonight inherits A/B's unsanitized state; Group 3's Access gate is the compensating control and the sanitizer remains a Group 5 verification item. No sanitization stage added.

## Group 3 — serve + secure (night shift, 2026-07-02)

Night-shift scope: build/stage everything that needs no sudo, browser, or other
machine; halt at each human wall and record it in the MANUAL_STEPS morning
runbook. The iMac and MBP were not touched.

### Built + tested tonight (committed, pushed)
- **Unit 0 — post_bash pass-detection fix.** Root cause found via a *real*
  captured PostToolUse event: piped/`-q` stdout (`33 passed in 0.54s`) has no
  `==== ====` banner, so the old `=+ (.*) =+` regex matched nothing and left
  `TESTED` stale (the 23:57 stop-hook loop). Now keys on outcome counts
  (`\d+ passed`, no `\d+ failed|error`). Fixture
  `tests/fixtures/post_bash_pass_event.json` + regression test. `stop_discipline.py`
  untouched.
- **Unit 2 — fail-closed Access gate** (`server/access_gate.py`, wired into
  `server/mcp_server.py`). `CONSTELLATION_REQUIRE_ACCESS=1` ⇒ every HTTP route
  except `/health` needs a `Cf-Access-Jwt-Assertion` that verifies (RS256, JWKS,
  aud+iss) or **403**; unconfigured-but-required also 403s. PyJWT + cryptography
  already in venv (no new dep). 13 unit tests.
- **Unit 4 — local verification.** `REQUIRE_ACCESS=0`: `/health` 200 + MCP
  `initialize` round-trip 200 (`mcp-session-id`, serverInfo "Constellation
  Memory", endpoint `/mcp`). `REQUIRE_ACCESS=1`: no token → 403, garbage → 403,
  `/health` still 200. Server killed; port 8000 clear.
- Test suite **166 → 181** (all green).

### Staged, NOT installed (need sudo / browser — see MANUAL_STEPS runbook)
- **Unit 1 —** `deploy/com.constellation.mcp.plist`, `deploy/com.constellation.tunnel.plist`,
  `deploy/install.sh`. System LaunchDaemons, run as `ama`, KeepAlive, RunAtLoad,
  abs `.venv` python, MCP bound `127.0.0.1:8000` only, logs to `data/logs/`. No
  secret in the world-readable plist; `install.sh` refuses while `REPLACE_ME`
  placeholders remain.
- **Unit 3 —** `deploy/cloudflared-config.yml`, named tunnel `constellation-air`,
  ingress `mcp.constellation-memory.com → http://127.0.0.1:8000`, catch-all 404.
  Tunnel UUID / creds are `REPLACE_ME` (created at morning `cloudflared` login).

### Human walls (morning runbook, dependency order)
cloudflared login/create/route → Cloudflare Access app (get AUD + team domain;
note the claude.ai web-callback caveat + FastMCP-OAuth / service-token fallback)
→ fill placeholders → `deploy/install.sh` (sudo) → device test matrix (web / iOS
/ Claude Code) → **only then** stop the iMac serving instance (dormant 30 days).
