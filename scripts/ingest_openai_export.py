#!/usr/bin/env python3
"""One-shot OpenAI (ChatGPT) export ingest.

Modern ChatGPT exports shard conversations across conversations-000.json ..
conversations-NNN.json. This parses every shard, diffs against the live index,
and folds ONLY the new conversations in via an incremental MiniLM/384 embed
(Option A — no re-embed of existing chunks, user-message embedding only for this
ingest; assistant-message / chunk_meta embedding is a separate approved sprint,
see docs/specs/chunk_meta_assistant_embedding.md).

Conversations present in the index but ABSENT from the export are treated as
OpenAI-side deletions and are NOT pruned (the index is their sole holder). Their
ids are recorded for the record.

Reuses scripts/nightly_ingest.incremental_embed so the embed/merge path,
notes-safety, and hot-reload stamping are identical to the nightly pipeline.
"""

import argparse
import datetime
import glob
import hashlib
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scripts'))

from core.config import DATA_DIR                    # noqa: E402
from core.chatgpt_parser import parse_chatgpt_export  # noqa: E402
import nightly_ingest as ni                          # noqa: E402


def _sha(path):
    try:
        return hashlib.sha256(open(path, 'rb').read()).hexdigest()
    except OSError:
        return None


def parse_export(export_dir):
    shards = sorted(glob.glob(os.path.join(export_dir, 'conversations-*.json')))
    if not shards:
        # fall back to a single conversations.json
        single = os.path.join(export_dir, 'conversations.json')
        if os.path.exists(single):
            shards = [single]
    export = {}
    for s in shards:
        for c in parse_chatgpt_export(s):
            export[c['id']] = c
    return shards, export


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('export_dir')
    ap.add_argument('--record-dir', default=None,
                    help='where to write the deletions record (default: export_dir parent)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    shards, export = parse_export(args.export_dir)
    corpus = json.load(open(os.path.join(DATA_DIR, 'conversations.json')))
    corpus_ids = {c['id'] for c in corpus}
    existing_chatgpt = {c['id'] for c in corpus if c.get('provider') == 'chatgpt'}

    export_ids = set(export)
    new_ids = export_ids - corpus_ids
    deleted_ids = existing_chatgpt - export_ids
    new_convs = [export[i] for i in sorted(new_ids)]

    report = {
        'export_dir': os.path.abspath(args.export_dir),
        'shards': len(shards),
        'export_conversations': len(export),
        'corpus_before': len(corpus),
        'chatgpt_before': len(existing_chatgpt),
        'new': len(new_ids),
        'deleted_absent_from_export': len(deleted_ids),
        'dry_run': args.dry_run,
    }

    # Record the OpenAI-side deletions (not pruned) for the record.
    record_dir = args.record_dir or os.path.dirname(os.path.abspath(args.export_dir))
    os.makedirs(record_dir, exist_ok=True)
    record_path = os.path.join(record_dir, 'deletions_absent_from_export.json')
    if not args.dry_run:
        with open(record_path, 'w') as f:
            json.dump({
                'recorded_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'export_dir': os.path.abspath(args.export_dir),
                'policy': 'NOT pruned — index is the sole holder of these '
                          'OpenAI-side deletions',
                'count': len(deleted_ids),
                'conversation_ids': sorted(deleted_ids),
            }, f, indent=2)
        report['deletions_record'] = record_path

    if args.dry_run:
        report['projected_after'] = len(corpus) + len(new_ids)
        report['projected_chatgpt_after'] = len(existing_chatgpt) + len(new_ids)
        print(json.dumps(report, indent=2))
        return 0

    if not new_convs:
        report['action'] = 'nothing new to ingest'
        print(json.dumps(report, indent=2))
        return 0

    notes_path = os.path.join(DATA_DIR, 'notes.json')
    notes_before = _sha(notes_path)

    from core.embedder import Embedder
    before, after = ni.incremental_embed(new_convs, Embedder())
    report['corpus_after'] = after
    report['embedded'] = len(new_convs)

    # Post-ingest facts.
    import numpy as np
    ce = np.load(os.path.join(DATA_DIR, 'chunk_embeddings.npy'))
    emb = np.load(os.path.join(DATA_DIR, 'embeddings.npy'))
    report['chunk_count'] = int(ce.shape[0])
    report['embedding_dim'] = int(emb.shape[1])
    report['notes_untouched'] = (notes_before == _sha(notes_path))
    report['action'] = f'ingested {len(new_convs)} new conversations'
    print(json.dumps(report, indent=2))
    return 0 if report['notes_untouched'] else 2


if __name__ == '__main__':
    sys.exit(main())
