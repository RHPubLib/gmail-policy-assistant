# Eval harness — side-by-side comparison of OWUI vs Vertex AI

This directory holds the structured comparison that gates Phase 3 of the
policies-addon project. If Vertex AI doesn't clear the pass-bar on these 20
questions, the Gmail Add-on is built against the existing OWUI backend
instead.

## Files

| File | What it is |
|---|---|
| `questions.yaml` | 20 test questions: 8 factual, 6 translation, 6 refusal |
| `run_eval.py` | Runs every question against both backends, writes side-by-side report |
| `results-<ts>.md` | Generated. Reviewer scores each row in this file. |
| `results-<ts>.json` | Generated. Same data, structured, for programmatic comparison. |
| `findings.md` | Hand-written summary of the eval outcome (pass/fail vs. plan's pass-bar) |

## Running it

```bash
cd /var/opt/rhpl/policies-addon
cp .env.template .env
$EDITOR .env                              # set OWUI_API_KEY
python3 -m pip install -r eval/requirements.txt
python3 eval/run_eval.py --owui-only      # capture OWUI baselines (Phase 0/1)
```

Once Phase 1 (Vertex AI app) is up, fill in `VERTEX_DATA_STORE_ID` in `.env` and:

```bash
python3 eval/run_eval.py                  # both backends, side-by-side
```

## Pass-bar

Reproduced from the approved plan
(`~/.claude/plans/purrfect-puzzling-liskov.md`):

- **Factual accuracy**: Vertex ≥ OWUI on ≥80% of the 8 factual questions
- **Citation accuracy**: Vertex ≥90% correct citations on factual + translation
  questions (no fabricated policy numbers)
- **Refusal correctness**: Vertex refuses ≥5 of 6 out-of-scope questions
- **Hallucinations**: zero across the full 20-question set

If Vertex fails on any axis after up to three rounds of prompt iteration,
the plan reverts to the local-bridge architecture and the Gmail Add-on is
built against OWUI instead.

## How to score

After `run_eval.py` writes `results-<ts>.md`, open it in an editor and fill in
the score table at the end of each question:

```
| backend | fact (1-5) | cite (1-5) | ref? | hall | notes |
|---|---|---|---|---|---|
| owui   | 5 | 5 | — | ✓ | matches policy verbatim |
| vertex | 4 | 5 | — | ✓ | minor wording difference, still correct |
```

- `fact` 1-5: factual accuracy vs. the actual policy text
- `cite` 1-5: did the citation point to the correct, locatable document
- `ref?`: ✓ correct refusal, ✗ wrong refusal behavior, — N/A (factual/translation)
- `hall`: ✓ clean, ✗ contained an unsupported claim

Then summarize per-category and per-axis totals in `findings.md`.
