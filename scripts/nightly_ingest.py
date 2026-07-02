#!/usr/bin/env python3
"""Nightly Claude Code ingest (Phase 2).

Discovers Claude Code session transcripts on this Air (~/.claude/projects) and,
read-only over the tailnet, on the MBP (ama@100.101.100.104:~/.claude/projects),
and folds new/changed sessions into the corpus.

Design guarantees
-----------------
* High-water manifest (data/nightly_manifest.json): each session keyed by its
  conversation id (local_<uuid>) with size/mtime/sha256. A session is NEW if the
  id is unseen, CHANGED if the sha differs, UNCHANGED otherwise. Re-runs with no
  changes touch nothing -> zero duplicates, idempotent.
* Same session present on both hosts -> one conversation id -> deduped; the
  freshest copy (newest mtime) wins.
* Embeds ONLY new/changed conversations; retained conversations keep their
  existing vectors. Clusters/edges/graph rebuilt over the union.
* Notes-merge safety invariant: notes.json is never written by this script;
  its sha256 is asserted identical before/after every cycle.
* conversations.json mtime is stamped LAST so a long-running server hot-reloads
  a fully-consistent generation on the next request.
* --dry-run reports the plan without parsing/embedding/writing/manifest updates.
* A flock lock file prevents overlapping runs.

Exit 0 on success (including "nothing to do"), non-zero on error.
"""

import argparse
import datetime
import fcntl
import hashlib
import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from core.config import DATA_DIR  # noqa: E402
from core.claude_code_parser import parse_session_file  # noqa: E402

LOCAL_PROJECTS = os.path.expanduser('~/.claude/projects')
MBP_SPEC = 'ama@100.101.100.104'
MBP_REMOTE = '.claude/projects/'          # relative to the MBP home dir
STAGING = os.path.join(PROJECT_ROOT, 'staging', 'mbp')
MANIFEST_PATH = os.path.join(DATA_DIR, 'nightly_manifest.json')
LOCK_PATH = os.path.join(DATA_DIR, 'nightly.lock')
REPORT_DIR = os.path.join(DATA_DIR, 'logs', 'nightly')

MIN_USER_MESSAGES = 1   # skip trivial/all-assistant sessions


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(path):
    try:
        return _sha256(path)
    except OSError:
        return None


def log(msg):
    print(f'[{_now_iso()}] {msg}', file=sys.stderr)


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def _is_session_file(fn):
    """True for a top-level session transcript. Excludes `agent-*.jsonl`
    subagent transcripts (Task/Agent spawns) — ephemeral tool output, not
    user-facing conversations."""
    return fn.endswith('.jsonl') and not fn.startswith('agent-')


def _walk_jsonl(root, source):
    found = []
    if not os.path.isdir(root):
        return found
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if _is_session_file(fn):
                path = os.path.join(dirpath, fn)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                found.append({
                    'source': source,
                    'path': path,
                    'session': os.path.splitext(fn)[0],
                    'conv_id': f'local_{os.path.splitext(fn)[0]}',
                    'size': st.st_size,
                    'mtime': st.st_mtime,
                })
    return found


def pull_mbp(report):
    """Read-only rsync pull of MBP session JSONLs into STAGING. Best-effort:
    if the MBP is unreachable the run continues with local only."""
    os.makedirs(STAGING, exist_ok=True)
    cmd = [
        'rsync', '-az', '--prune-empty-dirs',
        '--include=*/', '--include=*.jsonl', '--exclude=*',
        '-e', 'ssh -o BatchMode=yes -o ConnectTimeout=12',
        f'{MBP_SPEC}:{MBP_REMOTE}', STAGING + '/',
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            report['mbp_pull'] = 'ok'
            return True
        report['mbp_pull'] = f'rsync exit {r.returncode}: {r.stderr.strip()[:200]}'
    except (subprocess.TimeoutExpired, OSError) as e:
        report['mbp_pull'] = f'error: {e}'
    log(f"MBP pull failed ({report['mbp_pull']}); continuing local-only")
    return False


def discover(report):
    """Return {conv_id: entry} keeping the freshest copy per conversation id."""
    entries = _walk_jsonl(LOCAL_PROJECTS, 'local') + _walk_jsonl(STAGING, 'mbp')
    report['discovered_files'] = len(entries)
    by_id = {}
    for e in entries:
        cur = by_id.get(e['conv_id'])
        if cur is None or e['mtime'] > cur['mtime']:
            by_id[e['conv_id']] = e
    report['discovered_sessions'] = len(by_id)
    return by_id


# --------------------------------------------------------------------------- #
# Manifest / high-water diff
# --------------------------------------------------------------------------- #
def load_manifest():
    try:
        with open(MANIFEST_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def classify(by_id, manifest):
    """Split discovered sessions into new / changed / unchanged. sha256 is
    computed here (only for discovered files)."""
    new, changed, unchanged = [], [], []
    for conv_id, e in by_id.items():
        e['sha256'] = _sha256(e['path'])
        prev = manifest.get(conv_id)
        if prev is None:
            new.append(e)
        elif prev.get('sha256') != e['sha256']:
            changed.append(e)
        else:
            unchanged.append(e)
    return new, changed, unchanged


# --------------------------------------------------------------------------- #
# Incremental embed
# --------------------------------------------------------------------------- #
def incremental_embed(fresh_convs, embedder):
    """Re-embed only `fresh_convs` (new+changed), reuse existing vectors for the
    rest, rebuild clusters/edges/graph, and persist. Returns (before, after)
    corpus sizes."""
    import numpy as np
    from collections import defaultdict
    from core.indexer import (embed_conversations, build_clusters, build_edges,
                              build_graph_data, save_pipeline_output)

    conv_path = os.path.join(DATA_DIR, 'conversations.json')
    with open(conv_path) as f:
        existing = json.load(f)
    existing_emb = np.load(os.path.join(DATA_DIR, 'embeddings.npy'))
    ce_path = os.path.join(DATA_DIR, 'chunk_embeddings.npy')
    cm_path = os.path.join(DATA_DIR, 'chunk_to_conv.json')
    existing_ce = np.load(ce_path) if os.path.exists(ce_path) else None
    existing_cm = json.load(open(cm_path)) if os.path.exists(cm_path) else []

    id_to_row = {c['id']: i for i, c in enumerate(existing)}
    chunks_by_row = defaultdict(list)
    if existing_ce is not None:
        for ci, conv_idx in enumerate(existing_cm):
            chunks_by_row[conv_idx].append(existing_ce[ci])

    fresh_ids = {c['id'] for c in fresh_convs}
    retained = [c for c in existing if c['id'] not in fresh_ids]
    # On-disk convs have user_messages stripped (save_pipeline_output drops them);
    # build_graph_data / cluster labelling need them back. Reconstruct from the
    # user-role messages, matching what the parsers emit.
    for c in retained:
        if 'user_messages' not in c:
            c['user_messages'] = [m['text'] for m in c.get('messages', [])
                                  if m.get('role') == 'user']
    final_convs = retained + fresh_convs

    final_emb, final_ce, final_cm = [], [], []
    # retained: reuse existing vectors
    for i, c in enumerate(retained):
        old = id_to_row[c['id']]
        final_emb.append(existing_emb[old])
        vecs = chunks_by_row.get(old) or [existing_emb[old]]
        for v in vecs:
            final_ce.append(v)
            final_cm.append(i)

    # fresh: embed now
    if fresh_convs:
        new_emb, new_ce, new_cm = embed_conversations(fresh_convs, embedder)
        new_chunks = defaultdict(list)
        if new_ce is not None:
            for ci, conv_idx in enumerate(new_cm):
                new_chunks[conv_idx].append(new_ce[ci])
        base = len(retained)
        for j in range(len(fresh_convs)):
            final_emb.append(new_emb[j])
            vecs = new_chunks.get(j) or [new_emb[j]]
            for v in vecs:
                final_ce.append(v)
                final_cm.append(base + j)

    embeddings = np.array(final_emb)
    chunk_embeddings = np.array(final_ce) if final_ce else None
    cluster_info = build_clusters(embeddings)
    edges = build_edges(embeddings, final_convs)
    graph_data = build_graph_data(final_convs, embeddings, cluster_info, edges)
    save_pipeline_output(final_convs, embeddings, chunk_embeddings, final_cm,
                         graph_data, DATA_DIR)

    # Stamp conversations.json LAST so the server hot-reloads a consistent set.
    os.utime(conv_path, None)
    return len(existing), len(final_convs)


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def run(dry_run=False):
    os.makedirs(REPORT_DIR, exist_ok=True)
    report = {'started_at': _now_iso(), 'dry_run': dry_run,
              'host': os.uname().nodename}

    notes_path = os.path.join(DATA_DIR, 'notes.json')
    notes_sha_before = _sha256_bytes(notes_path)
    report['notes_sha_before'] = notes_sha_before

    pull_mbp(report)
    by_id = discover(report)
    manifest = load_manifest()
    new, changed, unchanged = classify(by_id, manifest)
    report.update(new=len(new), changed=len(changed), unchanged=len(unchanged))
    log(f"discovered {len(by_id)} sessions: "
        f"{len(new)} new, {len(changed)} changed, {len(unchanged)} unchanged")

    to_ingest = new + changed
    report['to_ingest'] = [e['conv_id'] for e in to_ingest]

    if dry_run:
        report['action'] = 'dry-run (no changes written)'
        _finish(report, notes_path, notes_sha_before)
        return report

    if not to_ingest:
        report['action'] = 'nothing to do'
        _finish(report, notes_path, notes_sha_before)
        return report

    # Parse the new/changed sessions.
    fresh_convs, parsed_ids, skipped = [], [], []
    for e in to_ingest:
        conv = parse_session_file(e['path'])
        if conv is None or len(conv.get('user_messages', [])) < MIN_USER_MESSAGES:
            skipped.append(e['conv_id'])
            continue
        fresh_convs.append(conv)
        parsed_ids.append(e['conv_id'])
    report['embedded'] = len(fresh_convs)
    report['skipped_trivial'] = skipped

    if fresh_convs:
        from core.embedder import Embedder
        embedder = Embedder()
        before, after = incremental_embed(fresh_convs, embedder)
        report['corpus_before'] = before
        report['corpus_after'] = after
        report['action'] = f'ingested {len(fresh_convs)} conversations'
    else:
        report['action'] = 'all candidates skipped as trivial'

    # High-water update: record every discovered session's current sha (parsed
    # ones and skipped-trivial alike, so trivial sessions aren't reparsed forever
    # unless they change).
    for e in by_id.values():
        manifest[e['conv_id']] = {
            'source': e['source'], 'path': e['path'], 'size': e['size'],
            'mtime': e['mtime'], 'sha256': e['sha256'],
            'last_seen': report['started_at'],
        }
    tmp = MANIFEST_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(manifest, f, indent=2)
    os.replace(tmp, MANIFEST_PATH)

    _finish(report, notes_path, notes_sha_before)
    return report


def _finish(report, notes_path, notes_sha_before):
    notes_sha_after = _sha256_bytes(notes_path)
    report['notes_sha_after'] = notes_sha_after
    report['notes_untouched'] = (notes_sha_before == notes_sha_after)
    report['finished_at'] = _now_iso()
    if not report['notes_untouched']:
        log('FATAL: notes.json changed during ingest — safety invariant violated')
    # Append the run report.
    stamp = report['started_at'].replace(':', '').replace('-', '')[:15]
    rp = os.path.join(REPORT_DIR, f'ingest_{stamp}.json')
    try:
        with open(rp, 'w') as f:
            json.dump(report, f, indent=2)
        report['report_path'] = rp
    except OSError:
        pass
    print(json.dumps(report, indent=2))


def main():
    ap = argparse.ArgumentParser(description='Nightly Claude Code ingest')
    ap.add_argument('--dry-run', action='store_true',
                    help='report the plan without writing anything')
    args = ap.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    lock_fd = open(LOCK_PATH, 'w')
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log('another nightly ingest is running (lock held); exiting')
        return 0
    try:
        report = run(dry_run=args.dry_run)
        if not report.get('notes_untouched', True):
            return 2
        return 0
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


if __name__ == '__main__':
    sys.exit(main())
