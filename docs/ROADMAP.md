# Constellation — Development Roadmap

> **This is a menu, not a commitment.** Items are grouped by task family,
> deliberately **NOT** ordered by chronology or priority. Scope consensus per
> item happens later, unhurried. Written for LLM consumption.
>
> **Status markers:** `[done]` `[spec exists]` `[blocked-by-X]` `[decision needed]`
> `[Mike action]` `[idea]`

Captured 2026-07-04 (~02:00), authority Mike. Corpus state at capture: **2,934
conversations** (claude 1278 / claude-code 49 / chatgpt 1150 / notebooklm 11 /
gemini 275 / aistudio 171), 19,650 chunks, 126.8 MB index, 51 notes. This file
is idea preservation, not tasking — nothing here was built during capture.

---

## 1. Conversation & Tags

- **chunk_meta structure**: per-chunk `role` / `provider` / `model` /
  `conversation_id` / epoch timestamps / modality refs. Replaces the parallel
  `chunk_to_conv.json` arrays with one index-aligned record per chunk.
  `[spec exists: docs/specs/chunk_meta_assistant_embedding.md; conversation-level
  provenance passthrough landed tonight]` — `save_pipeline_output` now preserves
  `model` / `inferred_grouping` / `system_instruction` across rebuilds.
- **Assistant-side embedding** (both sides of the conversation): ~2× chunks,
  role-filterable search required. Prerequisite for the speaker-provenance split
  experiment (user-register vs assistant-register visualization).
  `[spec exists; decision needed on search semantics]` — today only user messages
  embed; Gemini/NotebookLM/AI-Studio parsers already retain assistant turns in
  the message structures, so the content is staged for when embedding flips on.
- **Model identity tagging**: aistudio native `[done]` (171/171 carry
  `runSettings.model`, e.g. gemini-2.5-pro); OpenAI via adjacent assistant
  `model_slug` → `default_model_slug` fallback `[decision made, lands with
  chunk_meta]`; Claude export carries no model field → date-based era inference
  with confidence flag `[spec exists]`.

## 2. Security & Access

- **Cloudflare Access 730h session cycle**: first expiry ~2026-08-01/02, then
  monthly, browser re-auth per client, cannot be automated. Calendar ping STILL
  UNPLACED (client-side tool failures). `[Mike action + decision needed: reminder
  mechanism]`
- **`deploy/local/` gitignored pattern** for filled identifiers; tracked files
  carry `REPLACE_ME` placeholders only; `git grep` leak check before push.
  `[done tonight — record as standing convention]`
- **Single-executor rule**: one agent session per repo. `[convention, record]`
- **Orphan iMac tunnel** (constellation, `dca64438`, created 2026-03-12):
  decommission AFTER index reconciliation. `[blocked-by-iMac]`

## 3. Documentation & Anchors

- **Repo `README.md` + `CHANGELOG.md` as canonical source-of-truth**; anchor
  chats are semantic mirrors, updated on material change. `[decision made, files
  not yet created — idea → task]` (README.md exists in repo but not yet the
  curated source-of-truth; CHANGELOG.md not yet created.)
- **README anchor chat** exists on claude.ai (phrase: "constellation readme");
  awaiting Claude-side ingestion, then receives dated state notes.
  Build-history/changelog chat NOT yet created. `[Mike action]`
- **Note squash cadence**: consolidate BREADCRUMB/GRAVITY notes into dated
  changelog summaries, prune sources; suggest monthly, aligned with embed cron.
  `[decision needed]`

## 4. Embedding Model & Migration

- **Options**: A keep MiniLM/384 (current); B EmbeddingGemma local text-only
  (full re-embed ~19k chunks); C Gemini Embedding 2 cloud multimodal (unlocks
  2.56 GB sidecar; corpus transits Google API — privacy call). **Mixed-model
  index invalid: any switch = full re-embed.** `[blocked-by-iMac reconciliation;
  decision needed]`
- **Notes-across-re-embed procedure**: the notes sidecar is keyed independently
  and survived tonight's ingests (sha invariant held byte-for-byte across all
  D2–D5 embeds); formalize a tested invariant + squash-before-migrate step so
  breadcrumbs are never lost in a model migration. `[spec needed]`

## 5. iMac & Infrastructure Sunset

- **Reconcile** possibly-divergent (Gemini-embedded?) index variant on the iMac
  vs clyde-air MiniLM index. **THE gate for section 4.** `[Mike action: power it
  up; then agent task]`
- **Decommission sequence** after reconciliation: stdio server, tunnel,
  LaunchAgents; plus outstanding Group-4 items (MBP LaunchAgent teardown, iMac
  dormancy, Cloudflare registrant address update). `[blocked]`
- **Sync untangle**: iCloud Desktop/Documents × Google Drive nested sync loop on
  iMac/desktop. Policy already holding: ground-truth lives outside all synced
  paths. `[Mike action, daylight problem]`

## 6. Ingestion Automation

- **Claude Code session collector**: script to sweep `~/.claude/projects` JSONLs
  across machines (clyde-air, MBP, RPi5) into ground-truth + parser. claude-code
  provider at **49** is the corpus's biggest known gap — the build transcripts
  are the genotype. `[idea → near-term task]` (Foundations exist:
  `core/claude_code_parser.py` + `scripts/nightly_ingest.py` already sweep
  clyde-air + MBP nightly, excluding `agent-*` subagent transcripts; RPi5 remains
  deferred.)
- **Cloud-chat collector**: stray claude.ai remote conversations not yet in any
  export; define export cadence (monthly, aligned with cron) for Anthropic +
  OpenAI + Google. `[idea; decision needed on mechanism]`
- **Remaining providers**: Abacus, Grok exports; Typeless, SuperWhisper, Plaud
  transcripts + audio into ground-truth. `[Mike action to pull]`
- **AI Studio non-chat drafts** (11 single-prompt files): parked, revisit.
  `[idea]`

## 7. Audio / Multimodal Lane

- **1,679 ChatGPT voice WAVs (1.41 GB)** + the 58 voice-shell conversations
  (titled, dated, textless — the D2 protected class, archived at
  `ground-truth/openai/2025-08-24/`): local ASR (Whisper/Parakeet class,
  on-device) transcribes them into embeddable text for the EXISTING index — no
  cloud required, no model migration required. Mike builds exactly this pipeline
  professionally. `[idea, high-value, independent of section 4]`
- **Full media sidecar embedding** (images + audio semantically) rides on option
  C or a successor local multimodal model. `[blocked-by section 4]`

## 8. Visualization Resurrection

- **Locate the V3/V4 viz code** (Three.js frontend: Force 3D / 2D flat /
  temporal / clustered modes; provider-geometry schema: sphere / octahedron /
  dodecahedron / icosahedron). Confirm it still renders against the current data
  format. `[idea → audit task]` (Note: the geometry schema needs entries for the
  new providers — notebooklm / gemini / aistudio have no shape yet.)
- **On-demand render skill**: a `/constellation-render` capability for Claude
  Code (or Desktop) producing a dated PNG snapshot on request; snapshots archived
  in-repo. `[idea]`
- **Data-manipulation-with-visual-display skills**: filtered renders (by
  provider, by era, by tag). First target once tags exist: the speaker-provenance
  colored render. `[idea, depends on sections 1 and 8-audit]`
- **`/peek` formalized as a skill** (non-intrusive session JSONL tail). Proven
  working this session. `[idea → small task]`
