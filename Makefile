# Constellation make targets (Phase 0)

VENV_PY := .venv/bin/python

.PHONY: test merge-safe preflight health

test:
	$(VENV_PY) -m pytest tests/ -v

# Run the notes-merge regression suite; ONLY on success, touch the
# MERGE_SAFE flag that unlocks `--reembed` in guard_bash.py (rule 5).
# The flag is machine-local (gitignored) — every clone must earn it.
merge-safe:
	$(VENV_PY) -m pytest tests/test_notes_merge.py -v
	mkdir -p .claude/flags
	touch .claude/flags/MERGE_SAFE
	@echo "MERGE_SAFE flag set — re-embed unlocked on this machine."

preflight:
	scripts/preflight.sh

health:
	scripts/mcp_health.sh
