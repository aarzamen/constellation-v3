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
