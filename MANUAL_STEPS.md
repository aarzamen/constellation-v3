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
