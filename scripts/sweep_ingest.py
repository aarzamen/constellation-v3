#!/usr/bin/env python3
"""SWEEP v2 — one-shot multi-source fold-in (authority: Mike, 2026-07-04).

Folds four genuinely-new sources into the corpus in a SINGLE incremental embed.
All ingestion is additive: a parsed conversation is ingested only if its id is
absent from the existing corpus. No existing chunk is re-embedded; nothing is
deleted; notes.json is asserted byte-identical before/after (via the shared
`incremental_embed` harness in nightly_ingest).

Sources (delta by conversation id):
  * claude   — fresh Anthropic export (ground-truth/anthropic/2026-07-04/*.zip)
  * grok     — xAI export, normalized via grok_to_anthropic.py  [NEW PROVIDER]
  * gemini   — Gemini Apps MyActivity.json, entries strictly newer than the
               last-indexed gemini activity (UTC cutoff)
  * abacus   — Abacus ChatLLM export                            [NEW PROVIDER]

Verified 0-new (recorded, not ingested):
  * chatgpt      — 2026-07-03 shard export already indexed (corpus chatgpt=1150)
  * claude-code  — the 20 local_ Desktop convs already indexed

Run `--dry-run` to print the plan (no writes). Run with no flag to embed once.
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
    """Keep only conversations whose id is new to the corpus, deduped in-batch."""
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
    with open(os.path.join(DATA_DIR, 'conversations.json')) as f:
        corpus = json.load(f)
    existing_ids = {c['id'] for c in corpus}
    from collections import Counter
    before_prov = Counter(c.get('provider') for c in corpus)

    sources = [
        ('claude', source_claude),
        ('grok', source_grok),
        ('gemini', source_gemini),
        ('abacus', source_abacus),
    ]

    fresh, plan = [], []
    for name, fn in sources:
        parsed = fn()
        new = delta(parsed, existing_ids)
        # in-batch dedup across sources too
        new = delta(new, {c['id'] for c in fresh})
        fresh.extend(new)
        lo, hi = date_range(new)
        plan.append((name, len(parsed), len(new), lo, hi))

    print('\n=== SWEEP v2 ingest plan ===', file=sys.stderr)
    print(f'corpus before: {len(corpus)}', file=sys.stderr)
    for name, nparsed, nnew, lo, hi in plan:
        print(f'  {name:9} parsed {nparsed:5}  NEW {nnew:5}  [{lo} .. {hi}]',
              file=sys.stderr)
    print(f'  total NEW: {len(fresh)}  ->  corpus after: {len(corpus) + len(fresh)}',
          file=sys.stderr)

    result = {
        'corpus_before': len(corpus),
        'plan': [{'source': n, 'parsed': p, 'new': nn, 'from': lo, 'to': hi}
                 for n, p, nn, lo, hi in plan],
        'total_new': len(fresh),
        'provider_before': dict(before_prov),
    }

    if dry_run:
        result['action'] = 'dry-run (no writes)'
        print(json.dumps(result, indent=2))
        return result

    if not fresh:
        result['action'] = 'nothing to ingest'
        print(json.dumps(result, indent=2))
        return result

    from scripts.nightly_ingest import incremental_embed, _sha256_bytes
    notes_path = os.path.join(DATA_DIR, 'notes.json')
    sha_before = _sha256_bytes(notes_path)

    from core.embedder import Embedder
    embedder = Embedder()
    corpus_before_n, corpus_after_n = incremental_embed(fresh, embedder)

    sha_after = _sha256_bytes(notes_path)
    result.update(
        action=f'ingested {len(fresh)} conversations',
        corpus_after=corpus_after_n,
        notes_sha_before=sha_before,
        notes_sha_after=sha_after,
        notes_untouched=(sha_before == sha_after),
    )
    # provider-after breakdown
    with open(os.path.join(DATA_DIR, 'conversations.json')) as f:
        after = json.load(f)
    result['provider_after'] = dict(Counter(c.get('provider') for c in after))
    print(json.dumps(result, indent=2))
    if not result['notes_untouched']:
        print('FATAL: notes.json changed during ingest', file=sys.stderr)
        sys.exit(2)
    return result


def main():
    ap = argparse.ArgumentParser(description='SWEEP v2 one-shot ingest')
    ap.add_argument('--dry-run', action='store_true',
                    help='print the plan without writing anything')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
