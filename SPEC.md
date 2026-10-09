# SPEC: Ticket Triage Pipeline

## Goal
`python main.py --tickets tickets.json --schema label_schema.json` reads tickets, classifies them (structured LLM call), routes them deterministically (confidence < 0.65 -> human review), drafts replies for auto-triaged tickets only, saves every artifact, computes metrics, and validates itself. `python validate.py` re-checks the artifacts independently.

## Requirements checklist (TASK.md)
- [ ] 1. Load `tickets.json` + `label_schema.json`; deterministic preprocessing -> `preprocessed_tickets.json`
- [ ] 2. Classification call with labels from schema, JSON only, parsed + validated; raw + parsed outputs saved
- [ ] 3. Code-based routing (`< 0.65` or invalid -> `human_review`) -> `routing_decisions.json`
- [ ] 4. Reply call for `auto_triage` only; internal note for `human_review` -> `triage_results.json`
- [ ] 5. Metrics -> `evaluation_report.json`, `prediction_comparison.json`
- [ ] 6. One log line per LLM call -> `llm_calls.jsonl`
- [ ] 7. `python validate.py` with all 7 checks
- [ ] 8. Recovery: extract first JSON object from noisy text, then 1 stricter retry, failures logged
- [ ] 9. pytest: schema validation, routing threshold, metrics
- [ ] 10. CLI (argparse)
- [ ] 11. Stretch: `confusion_summary.json`
- [ ] 11 pipeline stages enforced in code

## Assumptions and decisions
- **Labels come only from `label_schema.json`**; nothing is hard-coded, so swapped fixtures work.
- **Expected labels are optional per ticket.** Metrics are computed over the tickets that have them, so fixtures without labels don't crash the pipeline.
- **Provider:** Groq via the `openai` package (OpenAI-compatible), model `openai/gpt-oss-20b`, `temperature=0`, JSON mode, 30s timeout. Key from `GROQ_API_KEY`. `LLM_BASE_URL` / `LLM_MODEL` env vars override this, so any OpenAI-compatible provider works.
- **Inputs at the repo root** (as in the CLI example); **artifacts go to `outputs/`** (`--out` flag), so a clean checkout plus one command regenerates everything.
- **Validation** uses pydantic, which is already installed with fastapi. The category and urgency values are checked against the loaded schema at runtime.
- **Internal note for human review is a code template** built from the routing reason, not an LLM call. It's deterministic, free, and can't produce a customer-facing reply by accident.
- **Replay:** raw outputs are saved under `outputs/raw/` named by `prompt_hash`. With `--replay`, an existing raw file is reused instead of calling the API. The run is the same pipeline code, just with no network and the same result.

## Design
**Stage enforcement:** `Pipeline` holds the current `Stage` (enum, in order). `advance(next)` raises if `next` isn't the allowed next stage. `ROUTED -> RESPONSE_GENERATED` happens only if any ticket was auto-triaged; otherwise it jumps to `RESULTS_SAVED`. The stage history is saved to `outputs/pipeline_run.json`.

| File | Responsibility |
|---|---|
| `triage/stages.py` | `Stage` enum + transition guard |
| `triage/preprocess.py` | load inputs; `clean_text` (strip, collapse whitespace, collapse repeated `!!!`/`???`), char/word counts |
| `triage/llm.py` | client, `prompt_hash`, raw-output saving, `llm_calls.jsonl` logging, replay |
| `triage/classify.py` | prompt from schema, `parse_classification` (json.loads -> extract first `{...}` -> pydantic + schema check), stricter retry |
| `triage/route.py` | `route(prediction)`: pure and deterministic |
| `triage/reply.py` | reply prompt (2-4 sentences, no invented facts/promises) + internal-note template |
| `triage/evaluate.py` | accuracy, review count, failure count, per-ticket comparison, confusion summary |
| `main.py` | CLI; runs the stages in order |
| `validate.py` | independent checks on `outputs/`; exit 1 on failure |
| `tests/test_core.py` | parse/validate, routing threshold, metrics: no network |

**Artifacts (`outputs/`):** `preprocessed_tickets.json`, `raw/*.json`, `predictions.json`, `routing_decisions.json`, `triage_results.json`, `prediction_comparison.json`, `evaluation_report.json`, `confusion_summary.json`, `llm_calls.jsonl`, `pipeline_run.json`.

## Build order (each slice runs and is committed)
1. Inputs (`tickets.json`, `label_schema.json`) + stages + preprocessing + CLI skeleton -> `preprocessed_tickets.json`
2. LLM layer + classification + parsing/recovery -> raw outputs, `predictions.json`, `llm_calls.jsonl`
3. Routing + replies/internal notes -> `routing_decisions.json`, `triage_results.json`
4. Evaluation + confusion summary + `validate.py`
5. Tests, README, `requirements.txt`, `.env.example`

## Testing
- `pytest`: parse good/noisy/invalid-label/garbage JSON; routing at 0.64 / 0.65 / invalid; metrics on a hand-built example.
- End to end: delete `outputs/`, run `main.py`, then `validate.py`.

## Out of scope
UI, async/batching, confidence calibration, rate-limit backoff beyond the single retry.
