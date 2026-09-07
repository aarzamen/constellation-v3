"""Generation-based index storage primitive.

Constellation — core/storage.py

Implements Task 1 of docs/superpowers/plans/2026-09-07-index-preservation.md:

- Immutable generation directories under ``data/generations/<id>/`` holding
  conversations, vectors, optional paired chunk vectors/map, graph, clusters,
  and a SHA256-validating manifest.
- A single atomic ``current.json`` pointer (schema 1, uuid4-hex generation).
- Compare-and-swap publication guarded by a stable ``index.lock`` flock; all
  staging, fsync, and validation happen outside the lock, which only compares
  the expected token and switches the pointer via ``os.replace``.
- Fail-closed reads: pointer or artifact corruption raises
  ``IndexCorruptError``; flat legacy layouts are accepted only when no
  pointer exists, with a change-detecting token.

Public API::

    load_snapshot(data_dir) -> IndexSnapshot | None
    publish_generation(data_dir, conversations, embeddings, graph_data,
                       chunk_embeddings=None, chunk_to_conv=None, *,
                       expected_generation, embedding_identity)
        -> IndexSnapshot
    read_generation_token(data_dir) -> str | None
    IndexSnapshot, StaleGenerationError, IndexCorruptError
"""

import copy
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass

import numpy as np

POINTER_SCHEMA = 1
POINTER_NAME = 'current.json'
LOCK_NAME = 'index.lock'
GENERATIONS_DIR = 'generations'
MANIFEST_NAME = 'manifest.json'

_GEN_ID_RE = re.compile(r'^[0-9a-f]{32}$')

# artifact filename -> required
_ARTIFACTS = {
    'conversations.json': True,
    'embeddings.npy': True,
    'graph_data.json': True,
    'clusters.json': True,
    'chunk_embeddings.npy': False,
    'chunk_to_conv.json': False,
}

_LEGACY_REQUIRED = ('conversations.json', 'embeddings.npy')
_LEGACY_OPTIONAL = ('graph_data.json', 'clusters.json',
                    'chunk_embeddings.npy', 'chunk_to_conv.json')


class StaleGenerationError(Exception):
    """The writer's expected generation no longer matches the pointer."""


class IndexCorruptError(Exception):
    """The on-disk index is present but invalid; fail closed."""


@dataclass(frozen=True)
class IndexSnapshot:
    token: str
    conversations: list
    embeddings: np.ndarray
    graph_data: dict
    clusters: dict
    chunk_embeddings: np.ndarray = None
    chunk_to_conv: list = None
    embedding_identity: dict = None
    directory: str = None

    def __post_init__(self):
        # Deep-own every mutable field so a writer caller cannot mutate a
        # published snapshot through retained references.
        object.__setattr__(self, 'conversations',
                           copy.deepcopy(self.conversations))
        object.__setattr__(self, 'graph_data', copy.deepcopy(self.graph_data))
        object.__setattr__(self, 'clusters', copy.deepcopy(self.clusters))
        object.__setattr__(self, 'chunk_to_conv',
                           copy.deepcopy(self.chunk_to_conv))
        object.__setattr__(self, 'embedding_identity',
                           copy.deepcopy(self.embedding_identity))
        object.__setattr__(self, 'embeddings',
                           _readonly_array(self.embeddings))
        if self.chunk_embeddings is not None:
            object.__setattr__(self, 'chunk_embeddings',
                               _readonly_array(self.chunk_embeddings))


def _readonly_array(arr):
    out = np.array(arr, copy=True)
    out.setflags(write=False)
    return out


# ---------------------------------------------------------------------------
# Validation

def _validate_identity(identity):
    if identity is None:
        return
    if not isinstance(identity, dict):
        raise ValueError('embedding_identity must be a dict or None')
    for key in ('model', 'policy'):
        val = identity.get(key)
        if not isinstance(val, str) or not val:
            raise ValueError(f'embedding_identity[{key!r}] must be a '
                             'non-empty string')
    rev = identity.get('revision')
    if rev is not None and not isinstance(rev, str):
        raise ValueError('embedding_identity revision must be str or None')


def _validate_matrix(arr, name, rows=None):
    if not isinstance(arr, np.ndarray) or arr.ndim != 2:
        raise ValueError(f'{name} must be a rank-2 ndarray')
    if arr.shape[1] <= 0:
        raise ValueError(f'{name} must have a positive dimension')
    if not np.issubdtype(arr.dtype, np.floating):
        raise ValueError(f'{name} must be floating point')
    if arr.size and not np.all(np.isfinite(arr)):
        raise ValueError(f'{name} contains non-finite values')
    if rows is not None and arr.shape[0] != rows:
        raise ValueError(f'{name} has {arr.shape[0]} rows, expected {rows}')


def _validate_conversations(conversations):
    if not isinstance(conversations, list):
        raise ValueError('conversations must be a list')
    ids = []
    for conv in conversations:
        if not isinstance(conv, dict):
            raise ValueError('each conversation must be a dict')
        cid = conv.get('id')
        if not isinstance(cid, str) or not cid:
            raise ValueError('conversation id must be a non-empty string')
        ids.append(cid)
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate conversation ids')
    return set(ids)


def _validate_chunks(chunk_embeddings, chunk_to_conv, n_convs, dim=None):
    if (chunk_embeddings is None) != (chunk_to_conv is None):
        raise ValueError('chunk_embeddings and chunk_to_conv must be '
                         'supplied together')
    if chunk_embeddings is None:
        return
    _validate_matrix(chunk_embeddings, 'chunk_embeddings')
    if dim is not None and chunk_embeddings.shape[1] != dim:
        raise ValueError(
            f'chunk_embeddings width {chunk_embeddings.shape[1]} does not '
            f'match conversation vector width {dim}')
    if not isinstance(chunk_to_conv, list):
        raise ValueError('chunk_to_conv must be a list')
    if len(chunk_to_conv) != chunk_embeddings.shape[0]:
        raise ValueError('chunk_to_conv length must match chunk rows')
    for v in chunk_to_conv:
        if isinstance(v, bool) or not isinstance(v, (int, np.integer)):
            raise ValueError('chunk_to_conv entries must be integers')
        if not 0 <= int(v) < n_convs:
            raise ValueError('chunk_to_conv index out of range')


def _validate_graph(graph_data, conv_ids, strict=True):
    if not isinstance(graph_data, dict):
        raise ValueError('graph_data must be a dict')
    nodes = graph_data.get('nodes')
    edges = graph_data.get('edges')
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError('graph_data must contain node and edge lists')
    node_ids = []
    for node in nodes:
        if not isinstance(node, dict) or not node.get('id'):
            raise ValueError('graph nodes must be dicts with ids')
        node_ids.append(node['id'])
    if len(set(node_ids)) != len(node_ids):
        raise ValueError('duplicate graph node ids')
    if strict and set(node_ids) != conv_ids:
        raise ValueError('graph nodes must match conversation ids exactly')
    known = set(node_ids) if not strict else conv_ids
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError('graph edges must be dicts')
        if edge.get('source') not in known or edge.get('target') not in known:
            raise ValueError('graph edge references unknown conversation')
    if strict:
        clusters = graph_data.get('clusters')
        if not isinstance(clusters, list):
            raise ValueError('graph_data.clusters must be a list')
        cluster_ids = set()
        for cl in clusters:
            if not isinstance(cl, dict) or isinstance(cl.get('id'), bool) \
                    or not isinstance(cl.get('id'), int):
                raise ValueError('clusters must be dicts with integer ids')
            cluster_ids.add(cl['id'])
        if len(cluster_ids) != len(clusters):
            raise ValueError('duplicate cluster ids')
        for node in nodes:
            cid = node.get('cluster')
            if cid is not None and cid not in cluster_ids:
                raise ValueError('node references unknown cluster')


def _validate_all(conversations, embeddings, graph_data,
                  chunk_embeddings, chunk_to_conv, embedding_identity):
    conv_ids = _validate_conversations(conversations)
    _validate_matrix(embeddings, 'embeddings', rows=len(conversations))
    _validate_chunks(chunk_embeddings, chunk_to_conv, len(conversations),
                     dim=embeddings.shape[1])
    _validate_graph(graph_data, conv_ids, strict=True)
    _validate_identity(embedding_identity)


# ---------------------------------------------------------------------------
# Filesystem helpers

def _fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError as e:
        if e.errno not in (errno.EINVAL, errno.EBADF):
            raise
    finally:
        os.close(fd)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _write_json(path, obj):
    with open(path, 'w') as f:
        json.dump(obj, f, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())


def _pointer_path(data_dir):
    return os.path.join(data_dir, POINTER_NAME)


# ---------------------------------------------------------------------------
# Pointer

def _read_pointer(data_dir):
    """Return generation id, None if absent; IndexCorruptError if malformed."""
    path = _pointer_path(data_dir)
    if os.path.islink(path):
        raise IndexCorruptError('current.json must not be a symlink')
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise IndexCorruptError(f'unreadable current.json: {e}')
    if not isinstance(data, dict) or data.get('schema') != POINTER_SCHEMA:
        raise IndexCorruptError('unsupported current.json schema')
    gen = data.get('generation')
    if not isinstance(gen, str) or not _GEN_ID_RE.match(gen):
        raise IndexCorruptError('malformed generation id in current.json')
    return gen


def _generation_dir(data_dir, gen_id):
    if not _GEN_ID_RE.match(gen_id):
        raise IndexCorruptError('invalid generation id')
    root = os.path.join(data_dir, GENERATIONS_DIR)
    if os.path.islink(root):
        raise IndexCorruptError('generations directory must not be a symlink')
    path = os.path.join(root, gen_id)
    if os.path.islink(path):
        raise IndexCorruptError('generation directory must not be a symlink')
    return path


# ---------------------------------------------------------------------------
# Legacy flat layout

def _legacy_token(data_dir):
    """Stable token over stat (size + mtime_ns) of all flat artifacts."""
    parts = []
    for name in _LEGACY_REQUIRED + _LEGACY_OPTIONAL:
        path = os.path.join(data_dir, name)
        try:
            st = os.stat(path)
        except FileNotFoundError:
            parts.append(f'{name}:absent')
            continue
        parts.append(f'{name}:{st.st_size}:{st.st_mtime_ns}')
    digest = hashlib.sha256('|'.join(parts).encode()).hexdigest()
    return f'legacy-{digest[:32]}'


def _legacy_presence(data_dir):
    present = [name for name in _LEGACY_REQUIRED + _LEGACY_OPTIONAL
               if os.path.exists(os.path.join(data_dir, name))]
    if not present:
        return False
    missing = [name for name in _LEGACY_REQUIRED if name not in present]
    if missing:
        # Any known flat artifact without the required core is a broken
        # index, not "no index".
        raise IndexCorruptError(
            f'partial flat legacy index: missing {sorted(missing)}')
    chunk_present = [name for name in
                     ('chunk_embeddings.npy', 'chunk_to_conv.json')
                     if name in present]
    if len(chunk_present) == 1:
        raise IndexCorruptError(
            f'partial flat legacy chunk pair: only {chunk_present[0]}')
    return True


def _load_legacy(data_dir):
    token_before = _legacy_token(data_dir)
    try:
        with open(os.path.join(data_dir, 'conversations.json')) as f:
            conversations = json.load(f)
        embeddings = np.load(os.path.join(data_dir, 'embeddings.npy'),
                             allow_pickle=False)
        graph_data = None
        graph_path = os.path.join(data_dir, 'graph_data.json')
        if os.path.exists(graph_path):
            with open(graph_path) as f:
                graph_data = json.load(f)
        clusters = None
        clusters_path = os.path.join(data_dir, 'clusters.json')
        if os.path.exists(clusters_path):
            with open(clusters_path) as f:
                clusters = json.load(f)
        chunk_embeddings = None
        chunk_to_conv = None
        if os.path.exists(os.path.join(data_dir, 'chunk_embeddings.npy')):
            chunk_embeddings = np.load(
                os.path.join(data_dir, 'chunk_embeddings.npy'),
                allow_pickle=False)
            with open(os.path.join(data_dir, 'chunk_to_conv.json')) as f:
                chunk_to_conv = json.load(f)
    except (OSError, ValueError) as e:
        raise IndexCorruptError(f'unreadable flat legacy index: {e}')

    try:
        conv_ids = _validate_conversations(conversations)
        _validate_matrix(embeddings, 'embeddings', rows=len(conversations))
        _validate_chunks(chunk_embeddings, chunk_to_conv, len(conversations),
                         dim=embeddings.shape[1])
        if graph_data is not None:
            _validate_graph(graph_data, conv_ids, strict=False)
    except ValueError as e:
        raise IndexCorruptError(f'invalid flat legacy index: {e}')

    if _legacy_token(data_dir) != token_before:
        raise IndexCorruptError('flat legacy index changed during read '
                                '(racing legacy writer)')
    return IndexSnapshot(
        token=token_before,
        conversations=conversations,
        embeddings=embeddings,
        graph_data=graph_data,
        clusters=clusters,
        chunk_embeddings=chunk_embeddings,
        chunk_to_conv=chunk_to_conv,
        embedding_identity=None,  # legacy vectors carry unverified identity
        directory=data_dir,
    )


# ---------------------------------------------------------------------------
# Generation load

def _load_generation(data_dir, gen_id):
    gen_dir = _generation_dir(data_dir, gen_id)
    if not os.path.isdir(gen_dir):
        raise IndexCorruptError(f'pointer references missing generation '
                                f'{gen_id}')
    manifest_path = os.path.join(gen_dir, MANIFEST_NAME)
    if os.path.islink(manifest_path):
        raise IndexCorruptError('manifest must not be a symlink')
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except (OSError, ValueError) as e:
        raise IndexCorruptError(f'unreadable manifest: {e}')
    if not isinstance(manifest, dict) or manifest.get('generation') != gen_id:
        raise IndexCorruptError('manifest does not match generation id')
    if manifest.get('schema') != POINTER_SCHEMA:
        raise IndexCorruptError('unsupported manifest schema')
    hashes = manifest.get('artifacts')
    if not isinstance(hashes, dict):
        raise IndexCorruptError('manifest missing artifact hashes')
    chunk_keys = [name for name in ('chunk_embeddings.npy',
                                    'chunk_to_conv.json') if name in hashes]
    if len(chunk_keys) == 1:
        raise IndexCorruptError(
            f'manifest chunk pair broken: only {chunk_keys[0]} hashed')
    for name, required in _ARTIFACTS.items():
        path = os.path.join(gen_dir, name)
        expected = hashes.get(name)
        if expected is None:
            if required or os.path.exists(path):
                raise IndexCorruptError(f'manifest missing hash for {name}')
            continue
        if os.path.islink(path):
            raise IndexCorruptError(f'artifact {name} must not be a symlink')
        if not os.path.exists(path):
            raise IndexCorruptError(f'missing artifact {name}')
        try:
            actual = _sha256(path)
        except OSError as e:
            raise IndexCorruptError(f'unreadable artifact {name}: {e}')
        if actual != expected:
            raise IndexCorruptError(f'hash mismatch for {name}')

    try:
        with open(os.path.join(gen_dir, 'conversations.json')) as f:
            conversations = json.load(f)
        embeddings = np.load(os.path.join(gen_dir, 'embeddings.npy'),
                             allow_pickle=False)
        with open(os.path.join(gen_dir, 'graph_data.json')) as f:
            graph_data = json.load(f)
        with open(os.path.join(gen_dir, 'clusters.json')) as f:
            clusters = json.load(f)
        chunk_embeddings = None
        chunk_to_conv = None
        if 'chunk_embeddings.npy' in hashes:
            chunk_embeddings = np.load(
                os.path.join(gen_dir, 'chunk_embeddings.npy'),
                allow_pickle=False)
            with open(os.path.join(gen_dir, 'chunk_to_conv.json')) as f:
                chunk_to_conv = json.load(f)
    except (OSError, ValueError) as e:
        raise IndexCorruptError(f'unreadable generation artifact: {e}')

    identity = manifest.get('embedding_identity')
    try:
        _validate_all(conversations, embeddings, graph_data,
                      chunk_embeddings, chunk_to_conv, identity)
    except ValueError as e:
        raise IndexCorruptError(f'invalid generation contents: {e}')

    graph_clusters = graph_data.get('clusters', [])
    if not isinstance(clusters, dict) \
            or clusters.get('k') != len(graph_clusters) \
            or clusters.get('clusters') != graph_clusters:
        raise IndexCorruptError(
            'clusters.json inconsistent with graph_data clusters')

    # Optional metadata: absent is fine, but present-and-wrong fails closed.
    dim = manifest.get('dimension')
    if dim is not None and (isinstance(dim, bool)
                            or dim != embeddings.shape[1]):
        raise IndexCorruptError('manifest dimension does not match vectors')
    rows = manifest.get('rows')
    if rows is not None and (isinstance(rows, bool)
                             or rows != len(conversations)):
        raise IndexCorruptError('manifest rows does not match conversations')

    return IndexSnapshot(
        token=gen_id,
        conversations=conversations,
        embeddings=embeddings,
        graph_data=graph_data,
        clusters=clusters,
        chunk_embeddings=chunk_embeddings,
        chunk_to_conv=chunk_to_conv,
        embedding_identity=identity,
        directory=gen_dir,
    )


# ---------------------------------------------------------------------------
# Public API

def load_snapshot(data_dir):
    """Load the current index. Returns None only when no index exists."""
    gen_id = _read_pointer(data_dir)
    if gen_id is not None:
        return _load_generation(data_dir, gen_id)
    # No pointer: flat legacy fallback only. Orphan generation dirs from a
    # failed first publish are ignored — they were never exposed.
    if not _legacy_presence(data_dir):
        return None
    return _load_legacy(data_dir)


def read_generation_token(data_dir):
    """Cheap reload check: pointer generation id, legacy token, or None."""
    gen_id = _read_pointer(data_dir)
    if gen_id is not None:
        if not os.path.isdir(_generation_dir(data_dir, gen_id)):
            raise IndexCorruptError(f'pointer references missing generation '
                                    f'{gen_id}')
        return gen_id
    if not _legacy_presence(data_dir):
        return None
    return _legacy_token(data_dir)


def publish_generation(data_dir, conversations, embeddings, graph_data,
                       chunk_embeddings=None, chunk_to_conv=None, *,
                       expected_generation, embedding_identity):
    """Validate, stage, and atomically publish a new generation.

    ``expected_generation`` is the token this build started from (None means
    the writer expects no index at all). A mismatch at switch time raises
    StaleGenerationError and leaves the current index untouched. Returns the
    committed IndexSnapshot directly; callers must not independently reload.
    """
    # Own every mutable input up front: the caller may mutate its objects at
    # any point after this call starts, and nothing staged, validated, or
    # returned may observe that.
    conversations = copy.deepcopy(conversations)
    graph_data = copy.deepcopy(graph_data)
    embedding_identity = copy.deepcopy(embedding_identity)
    chunk_to_conv = copy.deepcopy(chunk_to_conv)
    if isinstance(embeddings, np.ndarray):
        embeddings = np.array(embeddings, copy=True)
    if isinstance(chunk_embeddings, np.ndarray):
        chunk_embeddings = np.array(chunk_embeddings, copy=True)

    _validate_all(conversations, embeddings, graph_data,
                  chunk_embeddings, chunk_to_conv, embedding_identity)

    gen_id = uuid.uuid4().hex
    generations_root = os.path.join(data_dir, GENERATIONS_DIR)
    os.makedirs(generations_root, exist_ok=True)
    gen_dir = os.path.join(generations_root, gen_id)
    os.makedirs(gen_dir)

    published = False
    try:
        # Stage all artifacts outside the lock.
        embeddings = np.ascontiguousarray(embeddings)
        _write_json(os.path.join(gen_dir, 'conversations.json'), conversations)
        with open(os.path.join(gen_dir, 'embeddings.npy'), 'wb') as f:
            np.save(f, embeddings)
            f.flush()
            os.fsync(f.fileno())
        _write_json(os.path.join(gen_dir, 'graph_data.json'), graph_data)
        clusters_data = {
            'k': len(graph_data.get('clusters', [])),
            'clusters': graph_data.get('clusters', []),
        }
        _write_json(os.path.join(gen_dir, 'clusters.json'), clusters_data)
        if chunk_embeddings is not None:
            chunk_embeddings = np.ascontiguousarray(chunk_embeddings)
            with open(os.path.join(gen_dir, 'chunk_embeddings.npy'),
                      'wb') as f:
                np.save(f, chunk_embeddings)
                f.flush()
                os.fsync(f.fileno())
            _write_json(os.path.join(gen_dir, 'chunk_to_conv.json'),
                        [int(v) for v in chunk_to_conv])

        hashes = {}
        for name, required in _ARTIFACTS.items():
            path = os.path.join(gen_dir, name)
            if os.path.exists(path):
                hashes[name] = _sha256(path)
            elif required:
                raise IndexCorruptError(f'staging failed to produce {name}')
        manifest = {
            'schema': POINTER_SCHEMA,
            'generation': gen_id,
            'embedding_identity': copy.deepcopy(embedding_identity),
            'artifacts': hashes,
        }
        _write_json(os.path.join(gen_dir, MANIFEST_NAME), manifest)
        _fsync_dir(gen_dir)
        _fsync_dir(generations_root)

        # Load and validate the staged generation from disk before taking the
        # lock: what we return is exactly what any later reader will see, and
        # a bad staging never reaches the pointer.
        staged = _load_generation(data_dir, gen_id)

        # Lock only for compare-and-swap of the pointer.
        lock_path = os.path.join(data_dir, LOCK_NAME)
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            current = _read_pointer(data_dir)
            current_token = current
            if current is None and _legacy_presence(data_dir):
                current_token = _legacy_token(data_dir)
            if current_token != expected_generation:
                raise StaleGenerationError(
                    f'expected generation {expected_generation!r} but '
                    f'current is {current_token!r}')
            tmp_path = os.path.join(
                data_dir, f'.current.{uuid.uuid4().hex}.tmp')
            try:
                _write_json(tmp_path, {'schema': POINTER_SCHEMA,
                                       'generation': gen_id})
                os.replace(tmp_path, _pointer_path(data_dir))
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            # The pointer switch is the commit point: mark published before
            # the durability fsync so a failing fsync propagates its error
            # without deleting the now-visible generation.
            published = True
            _fsync_dir(data_dir)
        finally:
            os.close(lock_fd)
    finally:
        if not published:
            # Publication never happened; the staged directory was never
            # exposed, so removing it cannot affect any reader.
            shutil.rmtree(gen_dir, ignore_errors=True)

    return staged
