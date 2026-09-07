#!/usr/bin/env python3
"""One-shot OpenAI (ChatGPT) export ingest.

Modern ChatGPT exports shard conversations across conversations-000.json ..
conversations-NNN.json. This parses every shard and passes the FULL export
(existing ids included) through the shared ingest pipeline
(core.ingest.ingest_conversations), which reconciles against the retained
corpus: new records are added, extended histories adopted, divergent
histories preserved as revision records, and nothing already archived is
ever dropped.

Conversations present in the index but absent from the export are simply
"absent from this export" — absence is NOT evidence of deletion and nothing
is pruned. Their ids are recorded (after a successful commit, from the
committed snapshot) for the record. Export files are never deleted, archived,
or moved.

--dry-run runs a pure reconciliation preview (no model load, no publication,
no record file, no directory creation).
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
from core.reconcile import reconcile_conversations   # noqa: E402
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
    parsed = []
    for s in shards:
        parsed.extend(parse_chatgpt_export(s))
    # Pure duplicate validation before any dict construction can mask a
    # conflict: reconciling against an empty corpus raises ValueError on
    # duplicate native ids with conflicting history/provider, while
    # equivalent duplicates across shards coalesce.
    merged, _ = reconcile_conversations([], parsed)
    return shards, {c['id']: c for c in merged}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('export_dir')
    ap.add_argument('--record-dir', default=None,
                    help='where to write the absent-from-export record '
                         '(default: export_dir parent)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    shards, export = parse_export(args.export_dir)

    from core.storage import load_snapshot
    snap = load_snapshot(DATA_DIR)
    corpus = snap.conversations if snap is not None else []
    existing_chatgpt = {c['id'] for c in corpus
                        if c.get('provider') == 'chatgpt'}
    absent_ids = existing_chatgpt - set(export)
    candidates = [export[i] for i in sorted(export)]

    report = {
        'export_dir': os.path.abspath(args.export_dir),
        'shards': len(shards),
        'export_conversations': len(export),
        # Preview-time observations only; a concurrent writer may land before
        # the actual ingest. The commit-truth counts are set after ingest.
        'observed_corpus_before': len(corpus),
        'observed_chatgpt_before': len(existing_chatgpt),
        'dry_run': args.dry_run,
    }

    if args.dry_run:
        report['corpus_before'] = len(corpus)
        report['absent_from_export'] = len(absent_ids)
        # Pure reconciliation preview: no model instantiation, no
        # publication, no record write, no directory creation.
        merged, rreport = reconcile_conversations(corpus, candidates)
        report.update(rreport)
        report['projected_after'] = len(merged)
        print(json.dumps(report, indent=2))
        return 0

    notes_path = os.path.join(DATA_DIR, 'notes.json')
    notes_before = _sha(notes_path)

    from core.embedder import Embedder
    from core.ingest import ingest_conversations
    result = ingest_conversations(DATA_DIR, candidates, Embedder())

    # Report only committed facts, straight from the returned result.
    committed = result.snapshot
    report.update(result.report)
    report['corpus_before'] = result.before
    report['corpus_after'] = result.after
    report['embedded'] = result.embedded
    report['generation'] = committed.token
    report['chatgpt_after'] = sum(
        1 for c in committed.conversations if c.get('provider') == 'chatgpt')
    report['embedding_dim'] = int(committed.embeddings.shape[1])
    if committed.chunk_embeddings is not None:
        report['chunk_count'] = int(committed.chunk_embeddings.shape[0])
    # Observation only: this script does not write notes. A hash change is an
    # OBSERVED change whose cause cannot be inferred here — it may be a legacy
    # note migration or another writer, not necessarily authorized concurrent
    # note activity. It is not treated as a failure.
    report['notes_hash_changed'] = (notes_before != _sha(notes_path))
    report['action'] = (f'reconciled {len(candidates)} export conversations '
                        f'into the corpus')

    # Record ids absent from the export AFTER the successful commit, based on
    # the committed snapshot.
    committed_absent = sorted(
        c['id'] for c in committed.conversations
        if c.get('provider') == 'chatgpt' and c['id'] not in export)
    report['absent_from_export'] = len(committed_absent)
    record_dir = (args.record_dir
                  or os.path.dirname(os.path.abspath(args.export_dir)))
    record_path = os.path.join(record_dir, 'absent_from_export.json')
    # A record write failure after a successful commit must not fail the
    # import (the generation is already published); it is reported and the
    # publication is never retried.
    try:
        os.makedirs(record_dir, exist_ok=True)
        with open(record_path, 'w') as f:
            json.dump({
                'recorded_at':
                    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'export_dir': os.path.abspath(args.export_dir),
                'policy': 'Retained — these conversations were absent from '
                          'this export; absence is not confirmed deletion '
                          'and nothing is pruned.',
                'count': len(committed_absent),
                'conversation_ids': committed_absent,
            }, f, indent=2)
        report['absent_from_export_record'] = record_path
    except OSError as e:
        report['record_error'] = str(e)

    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
