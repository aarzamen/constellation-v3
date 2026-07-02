# MANUAL_STEPS.md

Human-required steps accumulated during Phase 0. Each entry: `### <short title>` / exact commands in a fenced block / one-line justification. Empty at sprint end = ideal outcome.

### Activate the Group 3 hooks (session restart)

```bash
cd /Users/ama/constellation-v3
claude   # then type /hooks and confirm the 5 hook entries are listed and approved
```

Hooks in `.claude/settings.json` are snapshotted at session start, so the session that installed them cannot live-fire them itself; every rule is instead proven by `tests/test_hooks.py` (44 synthetic stdin cases). On first restart, verify live: `python --version` must be blocked with the venv remedy, and ending a turn with uncommitted changes must be blocked by the Stop hook.
