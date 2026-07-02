# BASELINE.md — Phase 0 Pre-Sprint Snapshot

Captured: 2026-07-01T19:09 local (MBP M4 Max), commit `ad45e6b`, branch `main`, clean tree.
Backup: `backups/notes.json.pre-phase0.20260701T190843` (7,366 bytes).

## Notes inventory (data/notes.json)

**Total: 13 notes across 10 conversations.** Schema: `{conversation_id: [{text, created_at, note_id}]}`.

| Conversation | Count | note_ids |
|---|---|---|
| 047aa6e1-7bd3-431a-b8e7-0178849c1517 | 3 | 6e3e9efc, fa9bb804, 184d63d3 |
| 40f9434f-fa9a-466f-819a-9c1e6953b307 | 1 | 6882c041 |
| cf7cdb84-811d-4125-80a4-37cc7a955144 | 2 | bbbb9ad5, e903d8dd |
| b5b21b5d-3898-428f-a8d6-d199a87f4107 | 1 | bb694efa |
| 33dad95e-8aa7-46b1-9f62-4eabcb47152b | 1 | face018c |
| 7b5f4d69-e01e-448a-960a-a9ab6493696e | 1 | 3bf6be4e |
| c755edcb-b231-4e0d-bbec-cd7cc249ddc6 | 1 | 186ccfe4 |
| 970161db-3c48-45a5-923c-c7afc080d87e | 1 | a089ef0e |
| 3a5d9003-ec7d-4ac6-b9bc-425e5de036c8 | 1 | 8ed143ce |
| 6d656027-ff78-440f-9037-5a686ff01294 | 1 | a72139cb |

Note: the parent spec references dev-thread `3363bc73-af8f-45c2-a34e-13fed9ea53af` and note ids `06298723`, `f56c3e65`, `ae94e974`, `2532cf08`, `a16b4ebf`, `fafb880d`, `ec8b95dc` — **none of these appear in the local sidecar**. See Group 6 for reconciliation (they may live on the serving instance's dataset, not this machine's).

## get_stats (local engine, verbatim)

```json
{
  "totalConversations": 1698,
  "totalMessages": 25196,
  "dateRange": [
    "2023-12-13",
    "2026-03-07"
  ],
  "embeddingModel": "all-MiniLM-L6-v2",
  "embeddingDim": 384,
  "providers": {
    "claude": 975,
    "chatgpt": 723
  }
}
```

## pytest baseline

`101 passed in 13.49s` — 0 failures, 0 errors (suite: test_chatgpt_parser, test_constellation, test_gemini_parser, test_logger, test_multi_provider, test_notes).

## git log (top 20 at snapshot)

```
ad45e6b docs: STARTUP.md ground-truth guide + MCP HTTP transport support
fb1f038 v4.6: surgical fixes — security, naming cleanup, named tunnel, stderr discipline
078f9f1 v4.6: stderr prints, CDN pin, partial fixes
1566bf3 v4.6: helper fixes — named tunnel with quick tunnel fallback
3de1846 v4.6: README multi-provider section, config docs for additional sources
75a05d3 v4.6: security hardening — request size limit, XSS fix, CORS OPTIONS
8dfabb8 feat: add MCP server configuration and documentation for launching the server.
10d326c v4.5: tests for annotation protocol, list_recent_conversations, and docs
425225b v4.5: UI polish — status messages, uptime/PID display, layout cleanup
e2825cf v4.5: MCP annotation protocol in tool descriptions, ANNOTATION_PROTOCOL.md, DEPLOY.md
29f3bce v4.5: embedding progress dashboard — real-time PROGRESS protocol and helper UI
105bf8f v4.5: fix critical helper bugs — provider dropdown, startup hang, mislabeling, tunnel URL, stats display
e28272b v4.4: tests and documentation updates
8152426 v4.4: visualization layout fixes — cluster density, 2D spacing, helix scaling, clean transitions
4fb8c26 v4.4: preserve microsecond timestamps from Anthropic export — three-tier extraction
9625bf9 v4.4: structured logging with JSON file output, server-info and logs API endpoints
723dc8d v4.4: Constellation Helper — macOS control panel app with process management
f69c003 v4.3: export automation script and tools directory
5c6b29e v4.3: Grok stub parser with format auto-detection
81a06ba v4.3: Gemini AI Studio parser — directory scanning, extensionless JSON detection
```

## SuperInsights "zero commits across 11 sessions" audit — ANSWERED

Every top-level git repo under `/Users/ama` audited with `git log --since='2026-06-04' --oneline | wc -l` (no `~/dev` directory exists on this machine; glob adjusted to `/Users/ama/*/`):

| Repo | Commits since 2026-06-04 |
|---|---|
| TCCC_IOS | 83 |
| make an app | 60 |
| watch_wave | 37 |
| WristDeckCompanion | 16 |
| sota-surf | 7 |
| workflowwatch | 6 |
| wristwatch | 1 |
| constellation-v3 + 27 other repos | 0 each |

**Verdict: measurement gap, not a real audit failure.** 210 commits landed across 7 repos in the window. The June sessions simply worked in repos (or non-repo dirs) the tracker didn't inspect; constellation-v3 itself genuinely had 0 commits in the window (last commit `ad45e6b` predates it).

---

# FINAL — Phase 0 Closeout (2026-07-01)

## Note count reconciliation

| | Count | Detail |
|---|---|---|
| Baseline | **13** | 10 conversations (inventory above) |
| Healthcheck round-trip | +1 −1 | `[PHASE0-HEALTHCHECK]` note added and deleted by Group 2 — net zero |
| Widmark restore | **+0** | **The original `face018c` (2026-03-15) survives INTACT in this machine's sidecar** — the March loss destroyed the iMac's copy only. Nothing to restore locally; Phase 1's `merge_notes` union carries it back to the serving instance. |
| Hailo migration | ±0 | The 5 notes (`ae94e974`, `2532cf08`, `a16b4ebf`, `fafb880d`, `ec8b95dc`) exist only on the iMac's dataset; migration needs remote writes (forbidden in Phase 0) and has no confident target — deferred with candidates in MANUAL_STEPS.md |
| Closeout GRAVITY note | ±0 local | Target dev thread `3363bc73…` exists only on the iMac; remote write blocked by the sprint's own boundary — exact text staged in MANUAL_STEPS.md |
| **Final** | **13** | Verified identical to `backups/notes.json.pre-phase0.20260701T190843` (byte-for-byte diff) |

All movements accounted for: **13 = 13 + 0 restored + 0 migrated − 0 deleted.** No note was deleted this sprint.

## Key reconciliation discovery

The parent spec's Group 6 was written against the iMac's dataset. The Group 2 divergence guard measured it: public tunnel = (27,095 messages, max 2026-03-20) vs this machine = (25,196, 2026-03-07). Every spec-referenced note ID exists on the remote dev thread (verified read-only: 15 notes including `06298723`, `f56c3e65`, and the 5 hailo notes). Note `f56c3e65` (timestamps TODO) is **resolved** by Group 5's fix — recorded in the staged closeout note.

## pytest delta

| | Tests |
|---|---|
| Baseline | 101 passed |
| Final | **166 passed** (+7 MCP health, +44 hooks, +10 notes-merge, +4 timestamps) |

## Sprint commits

```
be3576f chore: phase 0 baseline snapshot
4c07d12 feat: scripts/preflight.sh — environment gate (Group 1)
251ceec feat: MCP health gate — stdio, binding, round-trip, divergence (Group 2)
631dae7 feat: guardrail hooks — venv/force/reembed guards + stop discipline (Group 3)
032bad0 fix: merge notes.json on re-embed (closes the Mar 29 data-loss bug)
047bd59 fix: get_conversation surfaces per-message timestamps (Group 5)
(+ this Group 6 closeout commit)
```
