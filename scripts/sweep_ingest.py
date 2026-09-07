#!/usr/bin/env python3
"""SWEEP v2 — one-shot multi-source fold-in (authority: Mike, 2026-07-04).

Folds four sources into the corpus in a SINGLE pass through the shared ingest
pipeline (core.ingest.ingest_conversations). ALL parsed conversations —
existing ids included — go through reconciliation: new records are added,
extended histories adopted, divergent histories kept as revision records, and
nothing already archived is ever dropped. Unchanged records reuse cached
vectors when the embedding identity matches. This script never writes
notes.json; its sha256 is compared before/after purely as an observation. A
hash change is OBSERVED only — its cause (legacy note migration, another
writer, concurrent note activity) cannot be inferred here.

Sources (delta by conversation id):
  * claude   — fresh Anthropic export (ground-truth/anthropic/2026-07-04/*.zip)
  * grok     — xAI export, normalized via grok_to_anthropic.py  [NEW PROVIDER]
  * gemini   — Gemini Apps MyActivity.json, entries strictly newer than the
               last-indexed gemini activity (UTC cutoff)
  * abacus   — Abacus ChatLLM export                            [NEW PROVIDER]

Verified 0-new (recorded, not ingested):
  * chatgpt      — 2026-07-03 shard export already indexed (corpus chatgpt=1150)
  * claude-code  — the 20 local_ Desktop convs already indexed

Run `--dry-run` to print the plan (source extraction still runs and writes to
staging, but there is no index publication). Run with no flag to embed once.
"""

import argparse
import json
import os
import subprocess
import sys
import zipfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from core.config import DATA_DIR                                   # noqa: E402
from core.parser import parse_claude_export                       # noqa: E402
from core.abacus_parser import parse_abacus_export                # noqa: E402
from core.grok_parser import parse_grok_export                    # noqa: E402
from core.gemini_activity_parser import extract_entries_json, sessionize  # noqa: E402

GT = os.path.join(PROJECT_ROOT, 'ground-truth')
WORK = os.path.join(PROJECT_ROOT, 'staging', 'sweep')

CLAUDE_ZIP = os.path.join(
    GT, 'anthropic', '2026-07-04',
    'data-ba821840-04bd-4972-ad33-04d8704d6c4a-1783195789-f3a50937-batch-0000.zip')
GROK_ZIP = os.path.join(GT, 'xai', '2026-07-04',
                        '63760187-abe8-4b68-8a05-7c985e7458ec.zip')
GROK_INNER = ('ttl/30d/export_data/38485f8c-5e30-4706-af11-33bc50136b91/'
              'prod-grok-backend.json')
GROK_CONVERTER = os.path.expanduser('~/dev/grok tool/grok_to_anthropic.py')
GEMINI_ZIP = os.path.join(GT, 'google', '2026-07-04',
                          'takeout-20260704T203616Z-3-001.zip')
GEMINI_INNER = 'Takeout/My Activity/Gemini Apps/MyActivity.json'
ABACUS_JSON = os.path.join(GT, 'abacus', '2026-07-04',
                           'abacus_chat_history_export-2.json')

# Last-indexed gemini activity: corpus max was local 2026-07-03T22:25:01 (PDT),
# i.e. UTC 2026-07-04T05:25:01.603000Z — this exact boundary entry is ALREADY
# indexed (under its old HTML wall-clock id), so the cutoff must exclude it.
# Full microsecond precision here so the string compare in source_gemini()
# excludes the boundary rather than admitting it via prefix-length ordering.
GEMINI_CUTOFF_UTC = '2026-07-04T05:25:01.603000Z'


def _extract(zip_path, inner, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extract(inner, dest_dir)
    return os.path.join(dest_dir, inner)


def source_claude():
    path = _extract(CLAUDE_ZIP, 'conversations.json', os.path.join(WORK, 'claude'))
    return parse_claude_export(path)


def source_grok():
    inner = _extract(GROK_ZIP, GROK_INNER, os.path.join(WORK, 'grok'))
    outdir = os.path.join(WORK, 'grok', 'normalized')
    subprocess.run([sys.executable, GROK_CONVERTER, inner, '--outdir', outdir],
                   check=True, capture_output=True, text=True)
    return parse_grok_export(os.path.join(outdir, 'conversations.json'))


def source_gemini():
    path = _extract(GEMINI_ZIP, GEMINI_INNER, os.path.join(WORK, 'gemini'))
    # entries carry UTC isoformat (no trailing Z); compare to the cutoff sans Z.
    cutoff = GEMINI_CUTOFF_UTC[:-1]
    entries = [e for e in extract_entries_json(path) if e['iso'] > cutoff]
    return sessionize(entries, gap_minutes=30)


def source_abacus():
    return parse_abacus_export(ABACUS_JSON)


def delta(convs, existing_ids):
    """Legacy helper (kept for reference, no longer in the active import
    path): keep only conversations whose id is new to the corpus, deduped
    in-batch. The active path now sends ALL parsed candidates through the
    shared reconciliation instead of filtering by id."""
    out, seen = [], set()
    for c in convs:
        cid = c['id']
        if cid in existing_ids or cid in seen:
            continue
        seen.add(cid)
        out.append(c)
    return out


def date_range(convs):
    ds = sorted(c.get('created_at', '') for c in convs if c.get('created_at'))
    return (ds[0], ds[-1]) if ds else ('-', '-')


def run(dry_run):
    from collections import Counter
    from core.reconcile import reconcile_conversations
    from core.storage import load_snapshot

    snap = load_snapshot(DATA_DIR)
    corpus = snap.conversations if snap is not None else []
    before_prov = Counter(c.get('provider') for c in corpus)

    sources = [
        ('claude', source_claude),
        ('grok', source_grok),
        ('gemini', source_gemini),
        ('abacus', source_abacus),
    ]

    # ALL parsed candidates go to reconciliation — no delta-by-id filtering,
    # so extended/diverged histories of existing ids are never lost.
    candidates, plan = [], []
    for name, fn in sources:
        parsed = fn()
        candidates.extend(parsed)
        lo, hi = date_range(parsed)
        plan.append((name, len(parsed), lo, hi))

    # Pure reconciliation preview for the plan (no publication).
    preview, rreport = reconcile_conversations(corpus, candidates)

    print('\n=== SWEEP v2 ingest plan ===', file=sys.stderr)
    print(f'corpus before: {len(corpus)}', file=sys.stderr)
    for name, nparsed, lo, hi in plan:
        print(f'  {name:9} parsed {nparsed:5}  [{lo} .. {hi}]',
              file=sys.stderr)
    print(f'  reconcile: {rreport}  ->  corpus after: {len(preview)}',
          file=sys.stderr)

    result = {
        # Preview-time observations; the actual import overwrites
        # corpus_before with the ingest's own commit-base count.
        'corpus_before': len(corpus),
        'observed_generation': snap.token if snap is not None else None,
        'plan': [{'source': n, 'parsed': p, 'from': lo, 'to': hi}
                 for n, p, lo, hi in plan],
        'reconcile_preview': rreport,
        'projected_after': len(preview),
        'observed_provider_before': dict(before_prov),
    }

    if dry_run:
        result['action'] = 'dry-run (no index publication)'
        print(json.dumps(result, indent=2))
        return result

    if not candidates:
        result['action'] = 'nothing to ingest'
        print(json.dumps(result, indent=2))
        return result

    from scripts.nightly_ingest import _sha256_bytes
    notes_path = os.path.join(DATA_DIR, 'notes.json')
    sha_before = _sha256_bytes(notes_path)

    from core.embedder import Embedder
    from core.ingest import ingest_conversations
    ingest = ingest_conversations(DATA_DIR, candidates, Embedder())

    sha_after = _sha256_bytes(notes_path)
    result.update(
        action=f'reconciled {len(candidates)} candidate conversations',
        corpus_before=ingest.before,
        corpus_after=ingest.after,
        embedded=ingest.embedded,
        reconcile=ingest.report,
        generation=ingest.snapshot.token,
        notes_sha_before=sha_before,
        notes_sha_after=sha_after,
        # Observation only: this script never writes notes. A change is
        # OBSERVED; its cause (migration, another writer, concurrent note
        # activity) cannot be inferred here. Not a failure.
        notes_hash_changed=(sha_before != sha_after),
    )
    result['provider_after'] = dict(Counter(
        c.get('provider') for c in ingest.snapshot.conversations))
    print(json.dumps(result, indent=2))
    return result


def main():
    ap = argparse.ArgumentParser(description='SWEEP v2 one-shot ingest')
    ap.add_argument('--dry-run', action='store_true',
                    help='print the plan (source extraction still writes to '
                         'staging; no index publication)')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
