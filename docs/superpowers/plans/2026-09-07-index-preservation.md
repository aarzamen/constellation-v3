# Import and Index Preservation Plan

Codex leads commands, verification, and Git. Native Claude Code authors scoped tests and code under the subagent-driven-development workflow. The user approved the architecture review and continuation; Pocket, live imports, host changes, deployment, push, and merge are excluded.

## Contract

Keep the existing archive when an incoming export omits conversations. Match message history by role and text, ignoring unstable message IDs. Exact histories merge useful metadata; strict extensions keep the existing ID; shorter histories cannot truncate it. Divergent histories preserve the original and create a deterministic revision ID with `revision_of` provenance. Reject ambiguous duplicate IDs or cross-provider collisions. Repeated imports must be idempotent. Rebuild derived user-message text from canonical messages.

Cache reuse requires identical embedding input and model/policy identity. A forced rebuild still retains all existing conversations. Legacy vectors have unverified identity and are re-embedded on upgrade. A changed model invalidates all retained vectors, even when dimensions match.

Publish immutable directories under `data/generations/<id>/` containing conversations, vectors, optional paired chunks/map, graph, clusters, and a validating manifest. A single atomic `data/current.json` pointer exposes the generation. Validate shapes, finite values, IDs, mappings, graph references, identity, and artifact hashes before publication. Fsync staged artifacts before switching. Retain old generations; no garbage collection in this pass.

Every writer supplies the generation it built from. A stable `index.lock` and compare-and-swap reject stale publication; retries rebuild from the latest corpus. Pointer corruption fails closed. Flat legacy data is accepted only when no pointer exists, with a token that detects changes. Partial flat indexes are errors, not empty archives. Notes stay at the data root and index locks must not enclose note locks. Legacy note migration runs before the first generation is published and cannot resurrect notes from obsolete flat files afterward.

SearchEngine captures one loaded state per operation, including lexical index and query embedding identity. It builds replacement state locally and swaps one reference. An in-flight query retains its original state. Failed loads do not damage previously loaded state. Chunk removal clears old chunk state. REST graph serving and reclustering use the same generation contract. Import reports describe their committed snapshot.

## Task 1: Storage primitive

- Native Claude authors synthetic tests in `tests/test_storage.py` for publication/readback, failure before pointer switch, stale concurrent writers, corrupt pointers/artifacts, invalid rows/maps/references, optional chunks, legacy fallback and partial legacy rejection.
- Codex observes red, then native Claude implements `core/storage.py` with the above contract. Public API: `load_snapshot(data_dir)` returns None only for no index, otherwise an IndexSnapshot; `publish_generation(data_dir, conversations, embeddings, graph_data, chunk_embeddings=None, chunk_to_conv=None, *, expected_generation, embedding_identity)` returns the committed IndexSnapshot. Snapshot fields include token, conversations, embeddings, graph_data, clusters, chunks/map, embedding_identity, directory. Expose StaleGenerationError and IndexCorruptError.
- Codex verifies tests and reviews the storage boundary before integration.

## Task 2: Reconciliation and adapters

- Native Claude authors regression tests before implementation: omitted providers retained, extension/shorter/divergent/idempotent imports, conflicts, forced rebuild preservation, model/content cache invalidation, atomic reader reload and in-flight query consistency, graph/recluster CAS, and notes not resurrected.
- Implement pure reconciliation and shared ingest pipeline; route launch, nightly, OpenAI and sweep imports through it. Stop adapters filtering out existing IDs. Update indexer publication, SearchEngine and HTTP readers/writers, embedder identity, and notes legacy guard. Preserve public signatures where practical and update fixtures to read through the storage API.
- No production corpus, real model downloads, servers, or network in tests. Use temporary synthetic archives and deterministic vectors. Existing safe selection starts at 199 tests.
- Independently review the complete diff; fix findings with regression tests, rerun affected checks and one broad safe suite. Update only relevant architecture documentation. Commit locally on `codex/constellation-preservation`; leave unmerged and undeployed.

## Limits and deferred work

No automatic pruning or generation cleanup. All index writers must be upgraded before deployment; old binaries writing flat files are not supported concurrently after migration. Model inference quality, real export import performance, power-loss durability and deployment remain unvalidated. Retrieval ranking, parser coverage, HTTP hardening, stable runtime layout and import UX are later work. The archive is not a backup until independently copied and restore-tested.

## Verification and handoff — completed locally

Native Claude Code authored scoped tests and implementation through the existing subscription; Codex ran commands, reviewed results, and handled Git. Storage and import regressions were observed failing before their fixes. Independent reviews identified and verified fixes for post-pointer-fsync cleanup, partial legacy validation, chunk coverage, model provenance and verification, revision collisions, small-corpus graph construction, first-publication note migration, committed-result reporting, and upstream ChatGPT duplicate suppression.

The final audited suite passes **295 tests**, up from the prior 199-test selection. It uses deterministic synthetic embeddings, owned temporary corpora, offline model settings, and blocked network calls. It covers separate-process publication contention, interrupted writes, concurrent request reload, import reconciliation and all prior note-preservation regressions. It excludes live MCP health probes, private-corpus spot checks and real inference. The full native branch review and the final scoped Codex review have no unresolved blockers. Whitespace validation passes.

Execution evidence and the runnable local audit harness are retained under `.superpowers/sdd/2026-09-07-index-preservation/` (gitignored). The original `/Users/ama/constellation-v3` checkout remains clean at `0b94c04`. This branch remains unmerged, unpushed and undeployed; no live archive, Air service, Pocket file, export package or host configuration was changed.

Before rollout, preserve and restore-test an independent archive copy and upgrade all active readers/writers together. A legacy index needs a full re-embed to acquire verified model identity. Reclustering legacy vectors alone preserves their unknown identity, so generated semantic search then requires re-embedding; browsing remains available. Old generations accumulate, including on otherwise unchanged imports; retention and an unchanged-import fast path are deferred. Missing or explicitly unverified model identities cannot authorize cache reuse or generated semantic queries.
