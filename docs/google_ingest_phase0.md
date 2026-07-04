# Google sources ingest — Phase 0–3 findings + Phase 4 HARD STOP

Authority: Mike, 2026-07-04. Executor: sole constellation session. **No embeds,
no index changes were made.** This is a review artifact; ingest awaits Mike.

## Phase 1 — archive (DONE, verified)

`/Users/ama/Desktop/Gemini Apps` → `ground-truth/google/2026-07-04/Gemini Apps`
(gitignored). Verified **2457 files / 2,823,677,878 bytes**, exact match on both.
Originals untouched (copy only).

## Phase 0 — inventory (read-only)

The Desktop "Gemini Apps" Takeout bundle is **heterogeneous** — three Google
products plus one misfiled non-Google export. File-type histogram: 788 json,
763 html, 322 png, 241 wav, 122 jpg, 60 zip, plus mp4/csv/pdf/xls and hundreds
of extensionless `<title>-<convhash>` attachment files.

### Which of the three requested sources are present

| Source | Present? | Form | Fidelity |
|---|---|---|---|
| **1. Gemini app chats** | **YES** | `MyActivity.html` (13.5 MB) Google "My Activity" log | **LOW** — user prompts only, flat, no grouping/model |
| **2. Google AI Studio** | **NO** (canonical) | `.prompt` files not present; **no Drive mount** at `~/Library/CloudStorage/GoogleDrive-*`; only `Developers/MyActivity.html` (322-entry activity fragment, mentions "aistudio") | n/a — canonical source missing |
| **3. NotebookLM** | **YES** (unexpected) | `NotebookLM/` (17 notebooks) + `NotebookLM2/` (30) dirs; **11 notebooks** have `Chat History/Chat Session-<uuid>.html` | **GOOD** — USER/MODEL turns preserved |

### Gemini `MyActivity.html` schema
- ~1650 `Prompted <text><br><timestamp>` entries; 1942 dated tokens.
- Timestamps: second precision + tz, e.g. `Jul 2, 2026, 5:09:06 PM PDT`.
- Date span **Dec 2020 → mid-2026** (year histogram 2020:1, 2024:153, 2025:1236, 2026:552).
- **No conversation ids, no conversation grouping, no assistant turns, no model.**
- "Attached N file" markers; the extensionless `<title>-<convhash>` files are the
  attachments but are not reliably linkable to a specific prompt from the log.

### NotebookLM
- 11 `Chat Session-<uuid>.html`, each with `USER:` / `MODEL:` labelled turns and
  real Q&A text. Notebook-level `metadata.json` has `createTime`/`lastViewed`;
  per-message timestamps appear absent. `NotebookLM` and `NotebookLM2` overlap by
  notebook title (two exports) → dedupe by title + session uuid.
- The other 36 notebooks hold Sources/Artifacts/Notes only (documents, not chats).

### ⚠ Misfiled non-Google data (bonus finding)
`conversations-e2a93f…json` and `conversations-70b2e0…json` (58.45 MB, **byte
-identical duplicates**) are **ChatGPT/OpenAI schema** (`mapping`, `gizmo_id`,
`sugar_item_id`, `default_model_slug`) — **781 conversations, NOT Gemini**.
Overlap with the current corpus chatgpt set: **723 present, 58 new-to-corpus.**
This is out of scope for the Google ingest; recommend a separate OpenAI top-up
decision for the 58. `memories-*.json` (account_uuid / conversations_memory /
project_memories) is likely associated with this ChatGPT data, not Gemini.

## Phase 2 — parser design (DESIGN ONLY; provisional pending Phase 0 review)

Target schema (existing): `{id, name, created_at, provider, messages[{role,text,
timestamp}], user_messages[]}`. **provider tag: `google`** (new; note the corpus
has no `google` yet, and the frontend has no `google` node shape — minor add).

### 2a. Gemini app chats (`MyActivity.html`)
- Parse each activity cell → a user message `{role:"user", text, timestamp}`
  (ISO-8601, preserving second precision; convert PDT/PST → UTC with offset kept).
- **Grouping (LOST) → remediation:** session-gap heuristic — consecutive prompts
  within a threshold (e.g. 30 min) form one conversation; a larger gap starts a
  new one. **Confidence: LOW.** Alternatives: 1 prompt = 1 conversation (fragments
  the corpus) or group-by-day (coarse). Flag every synthesized boundary.
- **Role (LOST):** user-only. Assistant turns are not in the log. This is
  consistent with the current user-only embedding, but the conversations will be
  half-transcripts. **Confidence: assistant content UNAVAILABLE.**
- **Model (LOST):** `model_slug = null`.
- **id (absent):** synthesize `google_gemini_<sha1(text+timestamp)>` (stable across
  re-runs for idempotency). Flag as synthetic.
- Attachments: record "N attachments" in text; do not attempt hash-linking.

### 2b. Google AI Studio
- Canonical `.prompt` files are the right source and are **not present locally**.
  The existing `core/gemini_parser.py` already targets AI Studio's chunkedPrompt
  format — likely reusable once the files arrive. **Recommend: DEFER** until Mike
  downloads the Drive "Google AI Studio" folder as a zip. The 322-entry
  `Developers/MyActivity.html` fragment is not worth parsing on its own.

### 2c. NotebookLM
- Each `Chat Session-<uuid>.html` → one conversation, `id google_nblm_<uuid>`,
  `name` = notebook dir title, messages parsed from USER/MODEL turns (roles
  **preserved**). Timestamps: per-message absent → fall back to notebook
  `createTime`/`lastViewed`. **Confidence: MEDIUM** (roles good, timing coarse).
- Dedupe across `NotebookLM`/`NotebookLM2` by (title, session uuid).

## Phase 3 — dry-run delta (no embed)

| Provider `google` sub-source | Conversations (est.) | Date range | Corpus overlap |
|---|---|---|---|
| Gemini app | grouping-dependent (~hundreds from 1650 prompts) | Dec 2020–Jul 2026 | **0** (no `google` provider) |
| NotebookLM | 11 | ~2025–2026 (notebook meta) | **0** |
| AI Studio | 0 local (deferred) | — | — |

Overlap vs the corpus is **~zero by construction** — no `google` provider exists.
(The only real overlap in the folder is the misfiled ChatGPT 781, which is not
this ingest.)

## Phase 4 — HARD STOP: recommendation (awaiting Mike)

1. **NotebookLM (11 sessions)** — highest fidelity (roles + real text). Best
   first candidate. Ingest as `google` / notebooklm.
2. **Gemini app (~1650 prompts)** — ingestable but LOSSY (user-prompts only,
   inferred grouping, no assistant/model). Worth it as searchable user-intent
   memory IF the caveats are acceptable. Recommend session-gap grouping + user-
   only embedding, every synthesized boundary flagged.
3. **AI Studio** — DEFER; Mike downloads the Drive folder → `.prompt` files →
   likely `core/gemini_parser.py`.
4. **Misfiled ChatGPT (58 new)** — separate OpenAI top-up decision, not Google.

**Nothing embedded, nothing indexed, nothing deleted.** Waiting for Mike's
decision on scope (which sub-sources, and the Gemini grouping tolerance).

---

## 2026-07-04 execution outcome (D1–D7)

Executed under Mike's 7-decision GO. Corpus **2477 → 2934** (+457); notes.json
**untouched** (sha invariant held); no deletions; no changes to existing chunks.

| Decision | Result |
|---|---|
| **D1 stash** | Kept private → `deploy/local/` (gitignored); 0 real values in tracked files. |
| **D2 recovered ChatGPT** | Export dated **2025-08-24** (max update_time), archived to `ground-truth/openai/2025-08-24/`. **PAUSED per D6**: all 58 new-to-corpus are CONTENTLESS (ChatGPT voice/`multimodal_text` + `system_error`; <20 chars text). Ids recorded (protected class); **not embedded** (nothing to embed). chatgpt stays **1150**, not 1208. |
| **D3 NotebookLM** | `core/notebooklm_parser.py`, provider `notebooklm`, **11** sessions, USER/MODEL roles preserved. |
| **D4 Gemini** | `core/gemini_activity_parser.py`, provider `gemini`. **No pre-Bard records** (activity runs 2024-12-11→2026-07-03; earlier "dates" were content, not activity timestamps). Every entry has an assistant response (not user-only). 30-min sessionization → **275** conversations (D6 OK, <2000), `inferred_grouping: True`. |
| **D5 AI Studio** | Zip already extracted at `~/Downloads/Google AI Studio` (archived to `ground-truth/google/2026-07-04/aistudio/`, 650 files/1.97 GB verified). 183 chunkedPrompt files → **172 chat / 11 non-chat**; parser `core/aistudio_parser.py`, provider `aistudio`, **171** ingested, native `model` carried (gemini-2.5-pro variants), `system_instruction` on 5. |
| **D6 sanity** | Triggered on D2 (58→0) → paused + reported + continued. Gemini 275 well under the 2000 ceiling. |
| **D7 verify** | 2934 convs · providers claude 1278 / claude-code 49 / chatgpt 1150 / notebooklm 11 / gemini 275 / aistudio 171 · chunks 19,650 · index 126.8 MB · notes 51 (canaries intact) · suite 194 passed / 1 skipped. |

`core/indexer.py` `save_pipeline_output` now preserves optional provenance fields
(`model`, `inferred_grouping`, `system_instruction`) so they survive rebuilds.
