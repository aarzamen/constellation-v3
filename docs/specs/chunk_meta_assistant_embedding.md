# Spec stub — `chunk_meta` + assistant-message embedding (next sprint)

**Status:** TODO / approved for the next sprint. NOT built in the 2026-07-03
OpenAI ingest, which stayed user-only per Decision 1.

## Motivation

Today the semantic tier embeds **user messages only**. Each row of
`chunk_embeddings.npy` is one user message; `chunk_to_conv.json[i]` maps chunk
`i` → its parent conversation index. There is no per-chunk metadata: we cannot
tell a chunk's role, which model produced an adjacent assistant turn, or weight
chunks differently. This blocks: assistant-answer retrieval, model-filtered
search, and role-aware ranking.

## Decision 1 — role tag (assistant embedding)

Add assistant messages to the semantic tier alongside user messages, tagged by
role, with a **lower confidence multiplier** for assistant chunks (see the
long-standing "Index assistant messages (weighted)" TODO in CLAUDE.md). For the
2026-07-03 OpenAI ingest we **kept user-only** embedding; this lands next sprint.

### Proposed `chunk_meta`

Replace the parallel-arrays representation (`chunk_to_conv.json`) with a single
`chunk_meta.json` — one record per chunk row, index-aligned with
`chunk_embeddings.npy`:

```json
{
  "conv": 1734,             // conversation index (== old chunk_to_conv[i])
  "role": "user",           // "user" | "assistant"
  "model_slug": null,       // see Decision 2
  "weight": 1.0             // 1.0 for user, <1.0 for assistant (tunable)
}
```

`chunk_to_conv.json` stays as a derived/compat artifact (`[m["conv"] for m in
chunk_meta]`) until all readers move to `chunk_meta`. Search applies `weight` as
a multiplier on the chunk-level cosine score before RRF fusion.

## Decision 2 — model tag (`model_slug` resolution)

For each embedded chunk, resolve `model_slug` as:

1. the **adjacent assistant message's** `model_slug`
   (ChatGPT export: `mapping[node].message.metadata.model_slug`; for a user
   chunk, the assistant reply that answered it);
2. else the conversation's **`default_model_slug`** (present on ChatGPT export
   conversation objects; confirmed on the 2026-07-03 export);
3. else **`null`**.

Claude / claude-code conversations have no per-message model slug in their
exports today → `null` unless a future export carries it. Store the raw slug
string; do not normalize (keep `gpt-4o`, `o3`, etc. verbatim) so model-filtered
search stays exact.

## Migration / compatibility

- Backfill: a one-time pass re-embeds assistant messages for existing convs and
  writes `chunk_meta.json`; user-chunk vectors are reused (no re-embed of the
  16,735 existing user chunks).
- `SearchEngine.load()` prefers `chunk_meta.json`, falls back to
  `chunk_to_conv.json` (role defaults to "user", weight 1.0, model null).
- The nightly/OpenAI ingest paths compute `chunk_meta` for new/changed convs at
  embed time (both parsers already expose per-message role; the ChatGPT parser
  additionally has model metadata in the mapping).
- Hot-reload sentinel (`conversations.json` mtime) is unaffected.

## Out of scope for this stub

Actual implementation, weight tuning, and the backfill job. This document only
records the two approved decisions so they land together with `chunk_meta`.
