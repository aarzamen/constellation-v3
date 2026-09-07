# Notes Preservation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Codex owns commands, verification, and Git; native Claude Code authors scoped tests and implementation.

**Goal:** Prevent concurrent note loss and prevent index rebuilds from undoing deliberate note deletion.

**Architecture:** Preserve the current notes.json format and identifiers. Serialize each whole note mutation across processes, write through unique temporary files, and keep derived-index rebuilds from rewriting notes. This is the first independently deliverable part of the approved preservation recommendations; import reconciliation and index publication follow separately.

**Tech Stack:** Python 3.12, standard-library file locking and unittest; existing NumPy dependency for synthetic pipeline fixtures.

**Spec:** `/Users/ama/Documents/ChatGPT/TCCC project/constellation-review-20260907/review.md`, with the user's subsequent correction: Pocket is excluded, the Air is Claude Code's playground, and its temporary field use does not establish a future hosting policy.

## Global Constraints

- Work only in the isolated `codex/constellation-preservation` worktree.
- No Pocket changes, phone interface work, host migration, live imports, service restarts, push, or merge.
- Use synthetic temporary corpora. Do not open production conversations, notes, secrets, or backups.
- No new runtime dependencies; preserve public note APIs and current eight-character note IDs.
- Worker receives source and tests only. Native Claude has no execution tools; Codex runs tests and applies its authored files.
- Retain corruption quarantine and existing read behavior. Mutations must fail without replacing a corrupt original if recovery is not possible; a failed quarantine must never authorize overwriting.
- Keep the backup before rebuilding. Stop unioning an old note snapshot back into the live sidecar. Keep the standalone merge utility available for existing callers/tests.

## Task 1: Serialize note mutations and respect deletion during rebuild

**Files:**
- Modify: `core/notes.py`
- Modify: `launch.py` (note backup/merge handling only)
- Create: `tests/test_notes_concurrency.py`
- Modify: `CLAUDE.md` (only the relevant note durability claims)

**Interfaces:** Preserve `load_notes(data_dir)`, `save_notes(data_dir, notes_dict)`, `append_note(data_dir, conversation_id, text)`, `delete_note(data_dir, conversation_id, note_id)`, `backup_notes(data_dir, retain=10)`, and `merge_notes(preserved, current)`.

- [x] Ask native Claude to author complete model-free regression tests for two overlapping successful additions, overlapping deletion/addition, concurrent legacy migration, replacement failure preserving the original, corrupt-file mutation refusing to discard original content, and a deletion during actual `run_pipeline_embed` remaining deleted. Tests must use bounded process/thread coordination with cleanup, not a barrier that deadlocks after locking is fixed.
- [x] Run those tests on the unchanged implementation and capture the expected failures. Use the existing synthetic pipeline approach: fake embedding and graph functions; patch `launch.DATA_DIR`, `ensure_data_dir`, and `save_config`; never load weights or call discovery.
- [x] Ask native Claude to implement the smallest fix. Use a stable separate lock file, hold its cross-process lock from reading through replacement for every mutation, and use unlocked private helpers to avoid nested-lock deadlocks. Keep read-triggered legacy migration under the same lock and recheck the sidecar after acquiring it. Use unique temporary paths with cleanup; flush and fsync before replacement. A rename failure must leave the original intact.
- [x] Remove `preserved_notes` snapshot merging from launch while retaining `load_notes` for legacy migration and `backup_notes` before rebuilding. No pipeline code may call `save_notes` after artifact publication.
- [x] Run the new tests, existing notes persistence/merge/backup/quarantine tests, browsing tests, and the established model-free nightly selection. Independently review the complete diff for lock coverage, exception paths, and side effects.
- [x] Correct CLAUDE.md's claim that atomic replacement alone makes simultaneous mutations safe. Explain cross-process locking and that rebuilds preserve the sidecar by leaving it untouched.
- [x] Commit the reviewed patch on the isolated branch using a conventional commit. Keep the branch unmerged and undeployed with explicit follow-up scope.

The next independent plan covers preserving all provider records, content-based cache invalidation, and complete index generation publication. Keeping those changes separate makes the highest-risk durable-note repair reviewable without coupling it to a storage migration.

## Verification and handoff

Native Claude authored the tests and implementation. Codex observed 9 of 10 strengthened tests fail against baseline, then 10 pass after the fix. A further legacy-note rebuild test failed before restoring startup migration. The final audited suite passes 199 tests with synthetic embeddings and network disabled. Independent task and whole-branch review found no blocking findings. This validates note persistence and pipeline mechanics, not real-model quality, live deployment, or power-loss durability. All cooperating writers must run the updated code before relying on the new advisory lock.

The patch stays on codex/constellation-preservation, unmerged, unpushed and undeployed. Pocket is excluded. No Air service, archive, export, backup or recurring task was changed. Hosting remains an independent follow-up: separate stable service code, durable archive storage, and experimental checkouts on the existing host before considering relocation.
