# Eval findings — Phase 2 decision (and Phase 5 re-eval)

**Phase 2 decision date:** 2026-05-12
**Phase 5 cutover date:** 2026-05-13
**Decision:** ✅ **PASS** at both checkpoints. Phase 3 (build the Gmail Add-on) and Phase 5 (cut over to live-source engine) both proceeded.

## Phase 5 update (2026-05-13)

The Phase 2 eval validated `your-policies-md` (GCS-sourced, Docling-converted
Markdown). At Phase 5 cutover we built a second engine, `your-policies-engine`,
ingesting from the Director's intranet folders directly (skipping `Old
Policies` + shortcuts to her private editable workspace). Re-ran the same
20-question eval against the live engine; it cleared the same pass-bar.

Behavioral differences between the two engines, scored on the same 20 questions:

| Question | your-policies-md (Phase 3) | your-policies-engine (shipping) |
|---|---|---|
| F5 sick days | "10 days/year per BENR-5" | "1 hour accrual per BENA-4" |
| T3 call in sick | Refused (couldn't find procedure) | **Answered with notification timeframes** |
| F7 hang up on abusive call | Cited CUS-2 *Guidelines* | Cited CUS-2 *Policy* (more authoritative) |
| F8 immediate family | Cited BENR-6 directly | Cited GEN-2 Definitions (canonical) |

On F5, the live engine's answer differs because the **intranet only publishes
BENA-4 (all employees)** — not BENR-5 (which only existed in the legacy
Docling MD set). The live answer is the Director's currently-authoritative
position, even though it gives a different number than the legacy answer.

This confirms the live-source architecture is doing what we designed it to:
answering from the Director's current published intent, not from a
once-converted snapshot.

---

## Scorecard

| Axis | Pass bar | OWUI baseline | Vertex (PDFs, iter 0) | Vertex (MDs, iter 1) | **Vertex (MDs, iter 2)** |
|---|---|---|---|---|---|
| Factual: Vertex ≥ OWUI on ≥80% of 8 | ≥80% | (n/a) | ~37% (4 tied, 3 transient 500s, 1 partial) | 75% (6/8) | **87.5% (7/8)** ✅ |
| Refusals clean | ≥5/6 | 4/6 | 5/6 | 5/6 | **6/6** ✅ |
| Citation accuracy | ≥90% | ~100% | (mostly missing) | ~71% | **~91%** ✅ |
| Hallucinations across 20 | 0 | 1 (R6 poem) | 0 | 0 | **0** ✅ |

## How we got here

1. **Phase 1 first ingestion** put 146 raw PDFs/DOCXs in a Vertex AI Search data store
   with Layout parser. Retrieval was **poor on personnel/HR docs** (sick leave, vacation,
   bereavement definition, call-in-sick, disciplinary) — Vertex couldn't surface the
   relevant doc even though it was indexed. Factual match-OWUI: 37%. Three transient
   HTTP 500s on a brand-new engine.

2. **Pivot to Docling-converted MDs:** uploaded the 157 MDs from
   `/path/to/kb-converted/` to `gs://your-policies-kb-bucket/_md/` via a JSONL
   manifest with explicit `mimeType: text/plain`. Created `policies-kb-md` data store +
   `your-policies-md` engine. **148 of 157 imported successfully**; 9 failed processing
   (likely empty or oddly-formatted MDs — not the ones referenced by the eval set).

3. **Iter 1 eval** against the MD engine jumped factual match-OWUI from 37% to 75% —
   confirms the MD path retrieves personnel HR content reliably where the PDF path
   missed it. Two remaining failures: F6 (maternity synthesis) and F7 (adversarial
   filter too aggressive).

4. **Iter 2 prompt iteration** (the iteration the plan allows):
   - Expanded vocabulary-translation table in the preamble (added maternity/paternity/
     parental → FMLA; was missing before).
   - Added explicit instruction: *"If the literal phrase isn't a policy heading, follow
     the umbrella policy."*
   - Disabled `ignoreAdversarialQuery` so questions about staff handling edge cases
     (F7) aren't swallowed by Vertex's adversarial filter.
   - Result: factual match-OWUI rose to 87.5%, refusal correctness to 6/6, citations
     to ~91%. All four pass-bar axes met.

## Known limitations being shipped with

- **F6 (maternity leave) edge case.** Vertex retrieval misses BENR-8 Leaves of Absence
  (FMLA) when the query says "maternity" (Vertex retrieves BENR-8 fine for "call in
  sick"). Pure semantic retrieval doesn't bridge "maternity" → "FMLA" the way OWUI's
  BM25-weighted hybrid retrieval does. **Safety outcome:** Vertex correctly says
  "I don't see a maternity-specific policy, contact the director" — no hallucination,
  just escalation. Real staff impact: a maternity question gets routed to the Director
  who will explain FMLA — same outcome as if no AI existed, just slightly less
  helpful than the OWUI answer.

  **Future mitigation ideas** (not blockers for shipping):
  - Add per-doc `covers_topics` metadata so semantic + keyword retrieval cooperate.
  - Maintain a small "topics index" markdown the Director can edit (`maternity → BENR-8`).
  - Re-ingest with chunk-level keyword enrichment.

- **9 of 157 MDs failed to import** — non-blocking for the eval (the policies tested
  were all in the 148 that imported). Worth diagnosing before Phase 4 rollout.

## What we ship to Phase 3
- **Backend:** Vertex AI Search Engine `your-policies-md` (data store `policies-kb-md`)
- **Model:** Gemini 2.5 Flash (stable)
- **Preamble:** the iter-2 version in `eval/run_eval.py`
- **Auth:** SA `policies-addon@your-gcp-project.iam.gserviceaccount.com`
- **Endpoint:** `:answer` on the engine's default serving config
- **Settings:** `ignoreAdversarialQuery=false`, `includeCitations=true`,
  `ignoreNonAnswerSeekingQuery=false`

## Reference artifacts
- OWUI + Vertex-PDF eval (iter 0): `results-20260512T132339Z.{md,json}`
- Vertex-MD iter 1: `results-20260512T135033Z.{md,json}`
- Vertex-MD iter 2 (the shipping configuration): `results-20260512T135950Z.{md,json}`
