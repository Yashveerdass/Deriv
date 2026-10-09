# Ticket Triage Pipeline: Submission Notes

A small, replayable AI ticket-triage pipeline. It reads support tickets and a label schema from local JSON files, cleans the text deterministically, classifies each ticket with a structured LLM call, routes low-confidence or invalid results to human review **using code rather than the model**, drafts replies only for auto-triaged tickets, saves every intermediate artifact, computes evaluation metrics, and validates its own outputs.

```
python main.py --tickets tickets.json --schema label_schema.json
python validate.py
```

<!-- TODO(final): confirm commands, flags and output dir against the finished code -->

---

## 1. How to run

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt        # macOS/Linux: .venv/bin/pip
copy .env.example .env                               # then set GROQ_API_KEY
.venv\Scripts\python main.py --tickets tickets.json --schema label_schema.json
.venv\Scripts\python validate.py
.venv\Scripts\python -m pytest -q
```

- All artifacts are written to `outputs/` (override with `--out`). The folder is git-ignored on purpose: the brief says static precomputed outputs are not enough, so every run regenerates them from the inputs.
- To use different fixtures, replace `tickets.json` / `label_schema.json` or pass other paths. No labels or wording are hard-coded.

<!-- TODO(slice 2): document --replay and the LLM env overrides once built -->

---

## 2. How I built it

I treated this like a small production task rather than a prompt demo: **spec first, then thin vertical slices, each one run, checked and committed before the next.**

### 2.1 Environment prepared before the session
- A project `.venv` with only the libraries I expected to need. A preflight script checked git identity, GitHub auth, imports, that `.env` exists and is git-ignored, and made a live API smoke test.
- **Secrets hygiene:** the API key lives only in `.env`, which is git-ignored. The AI assistant was configured (`.claude/settings.json`) so it could not read or edit `.env`, and the key was never printed in any command output.
- A written working agreement (`CLAUDE.md`) for the AI pair programmer: plan before code, about 100-line slices, stop after each slice for review, no new dependencies without asking, and if a fix fails twice, step back and simplify.

### 2.2 Spec before code
Before writing any code, I put the brief into `TASK.md` and wrote `SPEC.md`: a requirements checklist, assumptions, module design, artifact list, build order and test plan. Both were committed first (`758f31a`), so the git history shows the design came before the implementation.

### 2.3 Built in slices
| Slice | What it delivered | Commit |
|---|---|---|
| 1 | Input files, stage guard, loading + deterministic preprocessing, CLI skeleton | `3026ef0` |
| 2 | Classification LLM call, parsing/validation + recovery, raw-output saving, call logging | <!-- TODO --> |
| 3 | Deterministic routing, reply generation (auto only), internal notes | <!-- TODO --> |
| 4 | Evaluation metrics, comparison report, confusion summary, `validate.py` | <!-- TODO --> |
| 5 | Tests, README, `requirements.txt`, `.env.example` | <!-- TODO --> |

After every slice I ran the pipeline myself, inspected the generated JSON, and only then committed and pushed.

### 2.4 How I used AI
- **Implementer:** Claude Code in VS Code wrote each slice from my spec and the slice prompt, following the working agreement above.
- **Independent reviewer:** a second Claude Code session, which did not write the code, checked each slice against `TASK.md` / `SPEC.md`. It ran the code into a scratch folder and tried to break it. For example, it confirmed that the stage guard rejects a skipped stage, and it flagged that `ROUTED -> RESULTS_SAVED` was allowed unconditionally, which I then fixed.
- **What I owned:** the design and its tradeoffs, the slice boundaries, reading and running every change, and deciding what to accept, change or cut. AI output was treated as a draft to verify, not as an answer.

---

## 3. Design

### 3.1 Data flow and enforced stages
```
tickets.json + label_schema.json
        |
INIT -> INPUTS_LOADED -> TEXT_PREPROCESSED -> MODEL_PROMPTED -> STRUCTURED_OUTPUT_PARSED
     -> CONFIDENCE_CHECKED -> ROUTED -> RESPONSE_GENERATED* -> RESULTS_SAVED
     -> EVALUATION_COMPUTED -> VALIDATION_COMPLETED
                         (* skipped only if every ticket went to human review)
```
`triage/stages.py` defines the stages as an ordered enum. `Pipeline.advance()` raises if a stage is skipped or out of order, so the required sequence is enforced by code, not by convention. The stage history (with timestamps) is saved to `outputs/pipeline_run.json` as evidence of each run.

### 3.2 Modules
| File | Responsibility |
|---|---|
| `main.py` | CLI; runs the stages in order and writes artifacts |
| `triage/stages.py` | Stage enum + transition guard |
| `triage/preprocess.py` | Load and check inputs; deterministic cleaning and length stats |
| `triage/llm.py` | LLM client, prompt hashing, raw-output saving, `llm_calls.jsonl` logging |
| `triage/classify.py` | Schema-driven prompt; parse, validate, recover |
| `triage/route.py` | Deterministic routing rule |
| `triage/reply.py` | Reply prompt for auto-triage; internal-note template for human review |
| `triage/evaluate.py` | Metrics, per-ticket comparison, confusion summary |
| `validate.py` | Independent checks on the generated artifacts |
| `tests/` | Unit tests for the deterministic logic, no network |

<!-- TODO(final): reconcile module table with the actual files -->

### 3.3 Artifacts (`outputs/`)
| Artifact | Produced at stage | Contents |
|---|---|---|
| `preprocessed_tickets.json` | TEXT_PREPROCESSED | `ticket_id`, `original_text`, `cleaned_text`, `char_count`, `word_count` |
| `raw/…` | MODEL_PROMPTED | Raw model output for every LLM call |
| `predictions.json` | STRUCTURED_OUTPUT_PARSED | Parsed and validated classification, or the parse error |
| `routing_decisions.json` | ROUTED | `ticket_id`, `route`, `confidence`, `routing_reason` |
| `triage_results.json` | RESULTS_SAVED | Prediction, route, `customer_reply` or `internal_note` |
| `llm_calls.jsonl` | every LLM call | stage, ticket, timestamp, provider, model, prompt hash, artifact path |
| `evaluation_report.json` | EVALUATION_COMPUTED | Category/urgency accuracy, human-review count, parse failures |
| `prediction_comparison.json` | EVALUATION_COMPUTED | Expected vs predicted per ticket |
| `confusion_summary.json` | EVALUATION_COMPUTED | Most frequent category mix-ups |
| `pipeline_run.json` | every stage | Stage history with timestamps |

---

## 4. Key decisions and tradeoffs

| Decision | Why | Tradeoff |
|---|---|---|
| **Labels are read from `label_schema.json`, never hard-coded** | The evaluator may swap fixtures; the prompt and the validator both use the loaded schema | None worth noting |
| **Routing is a pure function in code** (`confidence < 0.65` or invalid output -> `human_review`) | The brief requires it; deterministic, testable, auditable. The model's `needs_human_review` is recorded but does not decide | Relies on the model's self-reported confidence (see limitations) |
| **Invalid model output never crashes the run** | One bad response shouldn't lose the whole batch; it becomes a human-review case with the error as the reason | Those tickets need a person |
| **Internal notes for human review come from a code template, not an LLM call** | Deterministic and free, and a human-review ticket can never accidentally get a customer-facing reply | Notes are plainer than LLM prose |
| **Two separate LLM calls (classify, then reply)** | Matches the brief. Replies are conditioned on validated labels and only generated for auto-triage | Two calls per auto ticket |
| **temperature 0 + every raw output saved and hashed** | Reproducibility and auditability: each prediction can be traced to the exact prompt and raw response | Same input does not always give byte-identical output from hosted models |
| **OpenAI-compatible client (Groq, `openai/gpt-oss-20b`)** | Free tier, fast, and the provider can be swapped by changing base URL / model | Smaller model than frontier ones |
| **Preprocessing keeps casing** | "URGENT" or capitals carry urgency signal | Slightly less normalisation |
| **Generated outputs are git-ignored** | The evaluator deletes and regenerates them; committing them would look like static output | Reviewers must run it to see results |

---

## 5. Reliability: parsing and recovery

Classification output goes through a fixed ladder, and every step is visible in the artifacts:

1. `json.loads` on the raw response.
2. If that fails, **extract the first `{...}` object** from noisy text (e.g. prose or code fences around the JSON).
3. **Validate**: required fields and types, `confidence` in [0, 1], and `category` / `urgency` **must be in the loaded schema**. Anything else is rejected.
4. If still invalid, **retry once with a stricter prompt**.
5. If still invalid, record a failed prediction with the error details. Routing sends it to `human_review` with that reason, and it is counted in `parse_validation_failures`.

<!-- TODO(slice 2): confirm each step against triage/classify.py and name the artifact fields that show it -->

---

## 6. Engineering practices used

- **Separation of concerns:** loading, preprocessing, the LLM call, parsing, routing, reply generation and evaluation are separate modules with one job each.
- **Pure, deterministic core logic:** cleaning, routing and metrics are plain functions with no I/O or network, so they are easy to unit test and give the same result every run.
- **Explicit state machine:** pipeline order is enforced by code, and illegal transitions fail loudly.
- **Validate at the boundaries:** input files are checked when loaded; model output is validated against the schema before anything downstream uses it.
- **Fail safe, not fail silent:** bad model output degrades to human review with a recorded reason rather than crashing or being silently accepted.
- **Configuration over hard-coding:** labels from the schema file, paths and output directory from CLI flags, provider and model from environment variables.
- **Traceability:** every LLM call is logged with a prompt hash and a pointer to its raw output file.
- **Secrets handled properly:** the key is read from the environment only, `.env` is git-ignored, `.env.example` documents the variables, and the key is never logged.
- **Minimal dependencies:** the `openai` client (for an OpenAI-compatible API), `python-dotenv`, and `pytest` for tests. <!-- TODO(final): match requirements.txt -->
- **Small, reviewable commits:** one slice per commit with an imperative message, pushed after each working step.
- **Independent validation:** `validate.py` re-checks the artifacts from disk instead of trusting the pipeline's own state.

---

## 7. Testing and validation

**Unit tests (`pytest`, no network)** <!-- TODO(slice 5): list actual tests and the result -->
- Classification parsing: valid JSON, JSON wrapped in noisy text, an invalid category, and garbage.
- Routing threshold: confidence 0.64 -> human review, 0.65 -> auto triage, invalid output -> human review.
- Metric calculation on a small hand-built example.
- Stage guard rejects a skipped stage.

**`python validate.py` checks** <!-- TODO(slice 4): confirm against the implementation -->
1. All required artifacts exist.
2. Every JSON file parses, and every `llm_calls.jsonl` line parses.
3. Every ticket has exactly one routing decision.
4. Every `auto_triage` ticket has a `customer_reply`.
5. Every `human_review` ticket has an `internal_note`.
6. Every predicted label belongs to the schema.
7. Evaluation metrics can be recomputed from the artifacts.

**End to end:** delete `outputs/`, run `main.py`, then `validate.py` and `pytest`. This is the evaluator's clean-checkout path.

---

## 8. Results on the sample tickets

<!-- TODO(final): paste metrics from evaluation_report.json after the final clean run -->
| Metric | Value |
|---|---|
| Tickets | 6 |
| Category accuracy | |
| Urgency accuracy | |
| Sent to human review | |
| Parse/validation failures | |

---

## 9. Requirements traceability

| # | Requirement | Where | Status |
|---|---|---|---|
| 1 | Load inputs + deterministic preprocessing -> `preprocessed_tickets.json` | `triage/preprocess.py`, `main.py` | Done (slice 1) |
| — | 11 stages enforced in code | `triage/stages.py` | Done (slice 1) |
| 2 | Structured classification, schema-validated; raw + parsed saved | `triage/classify.py`, `triage/llm.py` | |
| 3 | Deterministic routing -> `routing_decisions.json` | `triage/route.py` | |
| 4 | Replies for auto only; internal notes -> `triage_results.json` | `triage/reply.py` | |
| 5 | Metrics -> `evaluation_report.json`, `prediction_comparison.json` | `triage/evaluate.py` | |
| 6 | `llm_calls.jsonl` | `triage/llm.py` | |
| 7 | Validation command | `validate.py` | |
| 8 | Recovery path for malformed output | `triage/classify.py` | |
| 9 | Tests | `tests/` | |
| 10 | CLI | `main.py` | Done (slice 1) |
| 11 | Confusion summary (stretch) | `triage/evaluate.py` | |

---

## 10. Limitations and next steps

- **Confidence is self-reported by the model and not calibrated.** The 0.65 threshold is applied deterministically, but the score itself is a model guess. Next: calibrate the threshold on a larger labelled set, or combine it with signals such as label/explanation agreement or self-consistency across samples.
- **Six sample tickets is too few to judge accuracy;** one ticket moves accuracy by about 17 points. Next: a larger labelled evaluation set, including ambiguous and multi-issue tickets.
- **Reply guardrails are prompt-only.** Next: post-checks in code, e.g. sentence count 2-4 and a list of banned promises like "refund" or "we have fixed", with a fallback to human review.
- **No rate-limit backoff, and calls run one after another.** Fine for small batches. Next: backoff on 429 errors and parallel calls for larger inputs.
- **No PII redaction before sending text to the provider.** Next: mask emails, phone numbers and account numbers during preprocessing.
- **Hosted models are not perfectly deterministic even at temperature 0.** Raw outputs are saved so any run can be audited.
