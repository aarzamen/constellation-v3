"""Pure conversation reconciliation for the shared ingest pipeline.

Constellation — core/reconcile.py (Task 2 of
docs/superpowers/plans/2026-09-07-index-preservation.md).

Merges an incoming export into the retained corpus without ever dropping
existing conversations. Message histories are matched by (role, text) only;
unstable per-message ids and timestamps never affect matching or revision
identity.

Rules:
- Conversations present only in the archive are retained (preserve union).
- Exact history: merge non-empty incoming metadata, keep old omitted keys,
  including matched per-message metadata.
- Strict extension: keep the existing id, adopt the longer history, retain
  old per-message metadata on the shared prefix.
- Shorter (prefix) input leaves the old record fully unchanged ('stale').
- Divergent history keeps the original and adds a record with a
  deterministic UUID5 id derived from (provider, source id, role/text
  history), carrying `revision_of` provenance. Repeated imports of the same
  divergence are idempotent.
- Conflicts are validated before any dict overwrite: duplicate incoming
  native ids with conflicting history/provider, duplicate existing ids,
  cross-provider id collisions, and ambiguous revision-id collisions with
  reserved (existing or incoming) ids all raise ValueError.

Public API::

    reconcile_conversations(existing, incoming) -> (conversations, report)

Neither input list is mutated. The report counts new / extended / updated /
unchanged / stale / revisions. Output records carry rebuilt `user_messages`
derived from canonical messages; legacy embedded `notes` are dropped (notes
are migrated separately via the sidecar).
"""

import copy
import json
import uuid

_REVISION_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, 'constellation:revision')

REPORT_KEYS = ('new', 'extended', 'updated', 'unchanged', 'stale', 'revisions')

# Never overwritten by metadata merges.
_PROTECTED_CONV_KEYS = ('id', 'messages', 'user_messages', 'provider',
                        'revision_of', 'notes')
_PROTECTED_MSG_KEYS = ('role', 'text')


def _history(rec):
    return tuple((m.get('role'), m.get('text')) for m in rec['messages'])


def _user_messages(messages):
    return [m.get('text', '') for m in messages if m.get('role') == 'user']


def _normalize(conv):
    """Deep copy with validated id, defaulted provider, rebuilt derived
    fields, and legacy embedded notes removed."""
    rec = copy.deepcopy(conv)
    cid = rec.get('id')
    if not isinstance(cid, str) or not cid:
        raise ValueError('conversation id must be a non-empty string')
    msgs = rec.get('messages')
    if not isinstance(msgs, list):
        raise ValueError(f'conversation {cid!r} has no message list')
    if not rec.get('provider'):
        rec['provider'] = 'claude'
    rec['user_messages'] = _user_messages(msgs)
    rec.pop('notes', None)
    return rec


def _merge_conv_metadata(base, incoming):
    """Copy non-empty, non-protected incoming fields onto base."""
    changed = False
    for key, value in incoming.items():
        if key in _PROTECTED_CONV_KEYS:
            continue
        if value in (None, '', [], {}):
            continue
        if base.get(key) != value:
            base[key] = value
            changed = True
    return changed


def _merge_message_metadata(base_msgs, incoming_msgs):
    """Fill per-message metadata the old record lacks; role/text and any
    already-recorded values (e.g. original timestamps) are kept."""
    changed = False
    for old_msg, new_msg in zip(base_msgs, incoming_msgs):
        for key, value in new_msg.items():
            if key in _PROTECTED_MSG_KEYS or value in (None, '', [], {}):
                continue
            if key not in old_msg:
                old_msg[key] = value
                changed = True
    return changed


def revision_id(provider, source_id, history):
    """Deterministic UUID5 for a diverged history of a source conversation.

    Depends only on provider, the provider-native source id, and the exact
    (role, text) history — never on unstable message ids or timestamps — so
    independent archives assign the same revision id.
    """
    payload = json.dumps(
        [provider, source_id, [[role, text] for role, text in history]],
        ensure_ascii=False, separators=(',', ':'))
    return str(uuid.uuid5(_REVISION_NAMESPACE, payload))


def reconcile_conversations(existing, incoming):
    """Reconcile incoming conversations into the retained corpus.

    Returns ``(conversations, report)``; see module docstring for the full
    contract. Inputs are not mutated.
    """
    report = {key: 0 for key in REPORT_KEYS}

    by_id = {}
    order = []
    for conv in existing:
        rec = _normalize(conv)
        if rec['id'] in by_id:
            raise ValueError(f'duplicate existing conversation id '
                             f'{rec["id"]!r}')
        by_id[rec['id']] = rec
        order.append(rec['id'])

    # Validate and dedupe incoming ids BEFORE any merge into the corpus.
    inc_by_id = {}
    inc_order = []
    for conv in incoming:
        rec = _normalize(conv)
        prev = inc_by_id.get(rec['id'])
        if prev is not None:
            if (_history(prev) != _history(rec)
                    or prev['provider'] != rec['provider']):
                raise ValueError(
                    f'conflicting duplicate incoming id {rec["id"]!r}')
            _merge_conv_metadata(prev, rec)
            _merge_message_metadata(prev['messages'], rec['messages'])
            continue
        inc_by_id[rec['id']] = rec
        inc_order.append(rec['id'])

    # Prevalidate BEFORE any merge: an incoming record squatting on an
    # existing generated revision id may never extend or change its content
    # (deterministic revision ids must stay bound to their exact history);
    # rejecting up front makes both batch orders fail identically. The
    # original-id divergence replay path stays idempotent, and a changed
    # history must instead arrive under its source id to mint a new
    # deterministic revision.
    for cid, rec in inc_by_id.items():
        old = by_id.get(cid)
        if (old is not None and old.get('revision_of')
                and _history(old) != _history(rec)):
            raise ValueError(
                f'incoming conversation {cid!r} would change generated '
                f'revision of {old["revision_of"]!r}; reimport under the '
                'original conversation id instead')

    # Every existing and incoming native id is reserved; minted revision
    # ids may never collide with them ambiguously.
    reserved = set(by_id) | set(inc_by_id)

    for cid in inc_order:
        rec = inc_by_id[cid]
        old = by_id.get(cid)
        if old is None:
            by_id[cid] = rec
            order.append(cid)
            report['new'] += 1
            continue
        if old['provider'] != rec['provider']:
            raise ValueError(
                f'conversation id {cid!r} collides across providers '
                f'({old["provider"]!r} vs {rec["provider"]!r})')
        old_hist, new_hist = _history(old), _history(rec)
        if old_hist == new_hist:
            changed = _merge_message_metadata(old['messages'],
                                              rec['messages'])
            changed = _merge_conv_metadata(old, rec) or changed
            report['updated' if changed else 'unchanged'] += 1
        elif (len(new_hist) > len(old_hist)
                and new_hist[:len(old_hist)] == old_hist):
            merged_msgs = copy.deepcopy(rec['messages'])
            for old_msg, new_msg in zip(old['messages'], merged_msgs):
                for key, value in old_msg.items():
                    if key not in new_msg:
                        new_msg[key] = value
            old['messages'] = merged_msgs
            old['user_messages'] = _user_messages(merged_msgs)
            _merge_conv_metadata(old, rec)
            report['extended'] += 1
        elif (len(new_hist) < len(old_hist)
                and old_hist[:len(new_hist)] == new_hist):
            # Shorter prefix cannot truncate the archived history.
            report['stale'] += 1
        else:
            rev_id = revision_id(rec['provider'], cid, new_hist)
            existing_rev = by_id.get(rev_id)
            if existing_rev is not None:
                if (existing_rev.get('revision_of') != cid
                        or _history(existing_rev) != new_hist
                        or existing_rev['provider'] != rec['provider']):
                    raise ValueError(
                        f'revision id {rev_id!r} for {cid!r} collides with '
                        'an unrelated conversation')
                changed = _merge_message_metadata(existing_rev['messages'],
                                                  rec['messages'])
                changed = _merge_conv_metadata(existing_rev, rec) or changed
                report['updated' if changed else 'unchanged'] += 1
            elif rev_id in reserved:
                raise ValueError(
                    f'revision id {rev_id!r} for {cid!r} collides with an '
                    'incoming conversation id')
            else:
                rev = copy.deepcopy(rec)
                rev['id'] = rev_id
                rev['revision_of'] = cid
                by_id[rev_id] = rev
                order.append(rev_id)
                reserved.add(rev_id)
                report['revisions'] += 1

    return [by_id[cid] for cid in order], report
