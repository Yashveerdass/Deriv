# Ticket Triage Pipeline: Submission

> **Status: complete.** All five build slices and all ten review fixes are pushed. The acceptance suite passes **43/43** against a fresh clone (§7). This document reflects the code at `8276c89`.

## 1. What it is

A small, **replayable** AI pipeline for customer-support triage. It:

1. reads `tickets.json` and `label_schema.json` from disk;
2. cleans each ticket's text deterministically;
3. classifies **category, urgency and confidence** with a structured LLM call, validated against the schema;
4. routes each ticket **in code**: confidence `< 0.65` or invalid output goes to `human_review`, everything else to `auto_triage`;
5. drafts a short customer reply **only for auto-triaged tickets**, and writes an internal escalation note for the rest;
6. saves every intermediate artifact, computes evaluation metrics, and **validates its own output**.

```bash
python main.py --tickets tickets.json --schema label_schema.json   # run the pipeline -> outputs/
python validate.py                                                  # independent artifact checks
python -m pytest -q                                                 # offline unit tests
python run_acceptance_tests.py                                      # 43 end-to-end acceptance tests on a fresh clone
```

---

## 2. Running it

```bash
python -m venv .venv
.venv\Scripts\activate                     # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                     # then set GROQ_API_KEY (free key: console.groq.com)
python main.py --tickets tickets.json --schema label_schema.json
python validate.py
python -m pytest -q
python main.py --replay                    # re-run from saved model outputs, no API calls
```

| Flag / env var | Default | Purpose |
|---|---|---|
| `--tickets`, `--schema` | `tickets.json`, `label_schema.json` | Input files. Swap in any fixtures with the same structure |
| `--out` | `outputs` | Artifact folder. Git-ignored and regenerated on every run |
| `--model` / `LLM_MODEL` | `openai/gpt-oss-20b` | Model |
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | Any OpenAI-compatible endpoint |
| `GROQ_API_KEY` | (required for live calls) | Read from `.env`; never logged |
| `--replay` | off | Reuse saved raw outputs from `outputs/raw/` instead of calling the API |

---

## 3. Architecture

### 3.1 Stages, enforced in code
```
tickets.json + label_schema.json
  │
  INIT → INPUTS_LOADED → TEXT_PREPROCESSED → MODEL_PROMPTED → STRUCTURED_OUTPUT_PARSED
       → CONFIDENCE_CHECKED → ROUTED → RESPONSE_GENERATED* → RESULTS_SAVED
       → EVALUATION_COMPUTED → VALIDATION_COMPLETED
  * skipped only when no ticket was auto-triaged
```
`triage/stages.py` defines the stages as an ordered enum. `Pipeline.advance()` raises `Illegal stage transition` on any skipped or out-of-order step. The one permitted skip (`ROUTED → RESULTS_SAVED`) is allowed only when `response_optional` is set, and `main.py` sets it **right after routing, before any reply is drafted**, only if no ticket was auto-triaged. The stage history is written to `outputs/pipeline_run.json`.

### 3.2 Modules
| File | Responsibility |
|---|---|
| `main.py` | CLI. Each stage is a single-purpose function (`request_classifications`, `parse_classifications`, `generate_responses`, …); `main()` only wires them together and advances the stage guard |
| `triage/stages.py` | Stage enum and transition guard |
| `triage/preprocess.py` | Load and check inputs; `clean_text` (collapse whitespace and repeated `!!!` / `???` / `...`); char and word counts |
| `triage/llm.py` | The **only** module that talks to the provider: client config, `prompt_hash`, raw-output files, `llm_calls.jsonl`, replay. `call()` never raises; errors come back as values |
| `triage/classify.py` | Prompt built from the schema (normal and strict versions), `extract_json`, pydantic `Classification` model, label checks against the schema |
| `triage/route.py` | `route()`: pure, deterministic, threshold `0.65` |
| `triage/reply.py` | Reply prompt, `sentence_count`, internal-note template |
| `triage/evaluate.py` | Per-ticket comparison, metrics, confusion summary |
| `validate.py` | Re-checks the artifacts from disk; exit 1 on any failure. Also called in-process at the end of every run. Reports malformed records instead of crashing |
| `tests/` | 48 offline pytest tests (no network), including flow tests with a scripted `FakeLLM` and 14 validator tamper cases |
| `run_acceptance_tests.py` | One-command acceptance suite: 43 labelled tests (positive / negative / edge / abnormal) run against a fresh `git clone` |

### 3.3 Artifacts (`outputs/`)
| Artifact | Stage | Contents |
|---|---|---|
| `preprocessed_tickets.json` | TEXT_PREPROCESSED | `ticket_id`, `original_text`, `cleaned_text`, `char_count`, `word_count` |
| `raw/<stage>_<ticket_id>_<hash>.json` | per LLM call | Exact messages sent, raw output, error |
| `predictions.json` | STRUCTURED_OUTPUT_PARSED | Validated prediction or error, plus an **`attempts`** list (first try, strict retry) with each raw file and error |
| `routing_decisions.json` | ROUTED | `ticket_id`, `route`, `confidence`, `routing_reason` |
| `triage_results.json` | RESULTS_SAVED | Labels, confidence, route, `customer_reply` or `internal_note` |
| `prediction_comparison.json` | EVALUATION_COMPUTED | Expected vs predicted per ticket, correctness flags, parse error, retried |
| `evaluation_report.json` | EVALUATION_COMPUTED | Metrics + provider + model |
| `confusion_summary.json` | EVALUATION_COMPUTED | Most-confused pairs + expected→predicted matrix |
| `llm_calls.jsonl` | every call | stage, ticket, ISO timestamp, provider, model, prompt hash, artifact path, `replayed`, `error`. Reset each run |
| `pipeline_run.json` | end | Stage history with timestamps |

---

## 4. How each requirement is met

| Brief | Requirement | Implementation |
|---|---|---|
| 1 | Load inputs; deterministic preprocessing → `preprocessed_tickets.json` | `load_inputs` checks structure; `clean_text` is a pure function; counts from cleaned text |
| — | Stages enforced in code | `Pipeline.advance()` guard; conditional skip decided at routing time |
| 2 | Structured classification; labels from schema in prompt; JSON only; parsed + validated; invalid labels rejected; raw + parsed saved | `build_messages` lists `schema['categories']` / `urgency_levels` verbatim. JSON mode + "valid JSON only" instruction; pydantic types and confidence range; schema membership check; `raw/` + `predictions.json` |
| 3 | Deterministic routing; `< 0.65` → human review; invalid → human review with reason → `routing_decisions.json` | `route()` pure function. The model's `needs_human_review` is recorded in the reason but **never decides** |
| 4 | Reply (2–4 sentences, no invented facts or promises) **only** for auto; internal note for human review → `triage_results.json` | Second LLM call for `auto_triage` only, given the predicted labels. `sentence_count` check in code; a failed or off-spec reply is escalated, never sent. Notes from a code template, with no LLM call |
| 5 | Category/urgency accuracy, human-review count, parse failures; per-ticket comparison | `evaluate.compare` / `compute_metrics`; unlabelled tickets are excluded from accuracy instead of crashing |
| 6 | `llm_calls.jsonl`, one line per call with the required fields | `LLM.call()` appends after every call, live or replayed |
| 7 | Validation command | `python validate.py`: artifacts exist, valid JSON, one routing decision **and one result** per ticket, routes agree, auto ⇒ reply and confidence ≥ 0.65, human ⇒ note (and no reply), reply LLM calls only for auto tickets, log lines complete with existing raw files, labels in schema, metrics recomputed and matched to the report |
| 8 | Recovery for malformed output | ① `json.loads` → ② extract first `{…}` from noisy text → ③ validate → ④ **one strict retry** → ⑤ error result routed to human review. Visible in `predictions.json` `attempts` |
| 9 | Tests | 48 pytest tests (parsing, routing boundary, reply checks, metrics, input loading, stage guard, validator tamper cases, `FakeLLM` flow tests) + a 43-test acceptance suite |
| 10 | CLI | argparse: `--tickets --schema --out --model --replay` |
| 11 | Confusion summary (stretch) | `confusion_summary.json` |

---

## 5. Key decisions and tradeoffs

| Decision | Why | Tradeoff | Commit |
|---|---|---|---|
| **Spec before code** | Requirements, assumptions, design and build order agreed before any implementation | ~15 min of a 60-min session spent planning | `758f31a` |
| **Stages as an enforced state machine** | The brief says "enforce in code". An illegal order fails loudly, and the history is saved as evidence | Slightly more ceremony in `main.py` | `3026ef0`, `ad005bb` |
| **Labels only from `label_schema.json`** | The evaluator may swap fixtures; both the prompt and the validator read the file | None | `a302307` |
| **One provider module, OpenAI-compatible client (Groq, `gpt-oss-20b`)** | Free and fast; provider and model swappable by env var; every call goes through one logged path | Smaller model than frontier APIs | `a302307` |
| **JSON mode + pydantic + schema membership + one strict retry** | Reliable parsing without crashing; recovery is visible in the artifacts | One extra call for bad outputs | `a302307` |
| **`temperature=0`, prompt hashing, raw outputs saved, `--replay`** | Reproducibility and auditability: every prediction traces to the exact prompt and response, and a run can be replayed offline | Hosted models aren't perfectly deterministic even at temperature 0 | `a302307` |
| **Routing is a pure function in code** | Deterministic, testable, auditable. The model flag is advisory only | Relies on self-reported confidence | `f4f32e7` |
| **Replies only for auto, with a code-level sentence check; failure ⇒ escalate** | An auto ticket is never left without a reply, and an off-spec reply is never sent | Some escalations caused by reply format | `f4f32e7` |
| **Internal notes are templates, not LLM calls** | Deterministic and free, and an escalated ticket can't get a customer-facing reply | Plainer notes | `f4f32e7` |
| **`main.py` split into single-purpose stage functions** | Readable orchestration; each stage can be tested in isolation | — | `8f6bcd2` |
| **Unlabelled tickets excluded from accuracy, kept in the comparison** | Swapped fixtures without labels still run end to end | Accuracy is computed over a subset | `3b59833` |
| **Validation re-reads artifacts from disk; also runs in-process** | Independent of pipeline state; every run self-checks | Duplicated reads | `3b59833` |
| **Generated outputs git-ignored** | The brief says static outputs aren't enough; the evaluator deletes and regenerates them | Reviewers must run it to see outputs | `.gitignore` |
| **Not tuning the prompt to the 6 samples** | With 6 examples, prompt tweaks would overfit the eval set | Leaves a known billing/technical_issue confusion | — |

---

## 6. Reliability and safety

- **Never crashes on model output.** API errors, empty output, prose, invalid labels and out-of-range confidence all end as a recorded error, a route to `human_review`, and an internal note.
- **Replies are guarded twice:** the prompt rules from the brief, plus a sentence-count check in code. A reply that fails either way is not sent.
- **Traceable:** `llm_calls.jsonl` → `raw/` file → exact prompt and response, for every call.
- **Secrets:** the key is read from the environment only; `.env` is git-ignored, `.env.example` documents the variables, and the key is never printed or logged. The AI assistant was configured so it could not read `.env`.

---

## 7. Testing and verification

**Unit tests (48, offline, `pytest`).** Clean JSON; JSON in prose or code fences; invalid category, invalid urgency, confidence 1.5, garbage and `None` all rejected. Routing at 0.64, 0.65 and 0.99; invalid output goes to human review; the model flag alone doesn't force review. Reply checks for sentence count and commitments. Metrics and confusion summary on hand-built rows. Input-loading errors. The stage guard, including the conditional response-stage skip. 14 validator tamper cases. **End-to-end flow tests with a scripted `FakeLLM`:** retry then recover, double failure leading to escalation, low confidence with no reply call, reply API error leading to escalation.

**Acceptance suite (`python run_acceptance_tests.py`): 43/43 passed in 87s.** It clones the committed code into a temp folder (the evaluator's path), so it never touches `outputs/` or `.env`. Each test prints its type, what it checks, and the evidence:
| Group | Type | Covers | Result |
|---|---|---|---|
| U1–U5 | edge / negative / abnormal | Routing boundary 0.65 vs 0.6499; stage guard blocks an illegal skip; reply guard catches commitments (straight and curly apostrophes); 2–4 sentence rule; malformed output recovered or rejected | 5/5 |
| C1–C9 | negative / abnormal | Missing file, invalid JSON, missing field, duplicate ID, empty list, wrong type, empty schema, **no API key**, **unreachable provider**: each gives a clean `ERROR` with exit 1 and no traceback | 9/9 |
| A1–A11 | positive | Fresh clone: pytest; full live run; `validate.py`; exact stage order; every required field; preprocessing; routing rule; replies only for auto; call log; metrics recomputed independently; **`--replay` with no key gives identical results** | 11/11 |
| B1–B5 | edge | Swapped fixtures ("hi", messy text, unlabelled, Spanish + emoji, 2,800 chars, numeric ID); human-review path with no reply call; **a completely different label schema** (`payments`/`access`, `p1`/`p2`/`p3`) | 5/5 |
| D1–D13 | abnormal | 13 ways of tampering with artifacts, each caught by `validate.py` (FAIL, exit 1, no crash) | 13/13 |

**Independent review.** A second Claude Code session that did not write the code tested each slice:
| Test | Result |
|---|---|
| Live run, sample tickets | All stages complete; all required fields present; 12 calls logged, each pointing to an existing raw file |
| `--replay` | Identical `triage_results.json`; all calls marked `replayed` |
| **Swapped fixtures** (5 new tickets: shouty `!!!!` text, one-word "hi", two issues in one, unlabelled, ID rejection) | Ran with no code changes; ambiguous tickets (0.30 / 0.40) went to human review with notes and **no reply call**; the unlabelled ticket was excluded from accuracy |
| Fault injection (fake LLM) | Garbage then valid JSON → recovered on retry; garbage twice → human review; confidence 0.3 → no reply call; reply API error → escalated with a note |
| Tamper tests on `validate.py` (13 cases) | Caught 7 at first; the gaps became review fixes (§9); now 13/13 |
| Fresh `git clone` | A run with no key first exposed a silent-failure case (§9, #2); it now fails fast |

---

## 8. Results

**Sample tickets (6):**
| Metric | Value |
|---|---|
| Category accuracy | **0.83** (5/6) |
| Urgency accuracy | **0.67** (4/6) |
| Sent to human review | 0 |
| Parse/validation failures | 0 |
| Classification retries | 0 |

The only category confusion was **billing → technical_issue**: T2, "I withdrew funds… don't see them in my bank account", was read as a technical fault. Urgency was under-rated on T1 (locked out) and T4 (verification question). Model confidence was 0.92–0.95 on every sample ticket.

**Swapped fixtures (5, reviewer's set):** category accuracy 1.0 (4 labelled), urgency 0.75, **2 of 5 routed to human review** (the ambiguous "hi" and the two-issue ticket), 0 parse failures.

---

## 9. Review fixes (final hardening)

The independent review produced `TECHNICAL_ISSUES.md`. Every issue was fixed in a small commit, then verified by the acceptance suite:
| # | Issue | Fix | Commit |
|---|---|---|---|
| 1 | Stage guard allowed skipping RESPONSE_GENERATED unconditionally | Skip allowed only if routing auto-triaged nothing; decided before replies run (+2 tests) | `ad005bb` |
| 2 | No or invalid API key still "passed" with exit 0: all tickets escalated, accuracy 0 | Fail fast before any stage if the key is missing; exit 1 if every LLM call fails | `fae3bba` |
| 3 | `main.py` exited 0 when validation failed | Exit 1; `pipeline_run.json` records `validation_passed` and the failures | `fae3bba` |
| 4 | Some replies promised or claimed actions ("we will lock your account immediately") | Stricter prompt (no claimed or promised actions, no policy) + `reply_problem` code guard; a rejected reply leads to escalation | `fe304e4` |
| 5 | `validate.py` missed 5 of 13 tampering cases and crashed on 1 | Added checks: one result per ticket, routes agree, auto ⇒ confidence ≥ 0.65, reply calls only for auto, log fields + raw files; malformed records reported, not raised | `ad835fe` |
| 6 | Routing reason misleading after a reply-check escalation | Reason now starts with `escalated after reply check:` | `fe304e4` |
| 7 | Raw traceback on bad input files; duplicate IDs not rejected | Clean `ERROR:` + exit 1; duplicate, type and structure checks in `load_inputs` | `fae3bba` |
| 8 | No offline test of the pipeline flow | `FakeLLM` flow tests: retry, escalation, no reply call for review tickets | `697b8ad` |
| 9 | `requirements.txt` not pinned | Pinned to the tested versions | `1186e6c` |
| 10 | README claims and gaps | README updated for final behaviour, exit codes, link to this document | `1186e6c` |

---

## 10. How I built it

**Timeline (from the git history)**
| Time | Commit | Step |
|---|---|---|
| 16:50 | `a55066b` | Environment scaffold, before the task was known: `.venv`, packages, preflight script, `.gitignore`, working agreement |
| 17:02 | `0144e16` | Chose Groq as the LLM provider (free tier, OpenAI-compatible); preflight smoke test passed |
| 17:32 | `758f31a` | **Spec before code:** `TASK.md` (brief) + `SPEC.md` (requirements, assumptions, design, build order, test plan) |
| 17:36 | `3026ef0` | Slice 1: inputs, stage guard, preprocessing, CLI |
| 17:43 | `a302307` | Slice 2: LLM layer, structured classification, recovery, replay |
| 17:44 | `f4f32e7` | Slice 3: deterministic routing, replies, escalation notes |
| 17:46 | `8f6bcd2` | Refactor: single-purpose stage functions in `main.py` |
| 17:50 | `3b59833` | Slice 4: evaluation, confusion summary, `validate.py` |
| 17:50 | `cf8abca` | Slice 5a: offline tests |
| 17:51 | `d5795bb` | Slice 5b: README, requirements, `.env.example` |
| 17:58 | `ad005bb` | Review fix #1: conditional response-stage skip |
| — | `fae3bba` | Review fixes #2, #3, #7: fail fast on missing key, dead provider, bad inputs, failed validation |
| — | `fe304e4` | Review fixes #4, #6: no commitments in replies, code guard, clearer escalation reason |
| — | `ad835fe` | Review fix #5: hardened `validate.py` |
| — | `697b8ad` | Review fix #8: offline pipeline-flow tests with a scripted `FakeLLM` |
| — | `1186e6c` | Review fixes #9, #10: pinned requirements, README for final behaviour |
| — | `83af012` | Submission notes and technical-issues review committed |
| — | `8276c89` | Acceptance suite: 43 labelled end-to-end tests, all passing |

**Working method**
- **Environment first:** a preflight script checked git, GitHub auth, imports, that `.env` exists and is git-ignored, and made a live API call, so no time was lost on setup during the session.
- **Thin vertical slices:** each slice was run, inspected and reviewed before being committed and pushed.
- **A written working agreement** (`CLAUDE.md`) for the AI: plan before code, about 100-line slices, stop after each slice, no new dependencies without asking, simplify if a fix fails twice, never touch `.env`.

**How I used AI**
- **Implementer:** Claude Code in VS Code wrote each slice from `SPEC.md` and a slice prompt.
- **Independent reviewer:** a second Claude Code session, which never edited the pipeline code, checked every slice against the brief. It ran the code in a scratch folder, tried swapped fixtures, injected faults, tampered with artifacts, and tested a fresh clone. Its findings went back to the implementer as a written issue list (`TECHNICAL_ISSUES.md`) of concrete fixes. It also wrote the acceptance suite (`run_acceptance_tests.py`).
- **What I owned:** the design and tradeoffs, the provider choice, slice boundaries, running and reading every change, deciding what to accept or push back on, and choosing *not* to overfit the prompt to six examples.

---

## 11. Engineering practices

- **Separation of concerns:** one job per module; `main.py` only orchestrates.
- **Pure, deterministic core:** `clean_text`, `parse_classification`, `route` and the metrics have no I/O, so they're easy to test and give the same result every run.
- **Explicit state machine** for pipeline order.
- **Validate at boundaries:** input files on load; model output against a schema before use; artifacts re-checked from disk.
- **Fail safe, not fail silent:** bad output degrades to human review with a recorded reason.
- **Configuration over hard-coding:** labels from the schema file, paths from flags, provider and model from env vars.
- **Traceability:** prompt hashes, raw outputs, per-call log, stage history.
- **Reproducibility:** `temperature=0`, saved raw outputs, `--replay`.
- **Secrets hygiene:** env-only key, git-ignored `.env`, documented `.env.example`.
- **Minimal, pinned dependencies:** `openai`, `pydantic`, `python-dotenv`, `pytest`, at exact tested versions.
- **Tested at three levels:** pure-function unit tests, `FakeLLM` flow tests, and a live acceptance suite on a fresh clone.
- **Small, reviewed commits** with imperative messages, pushed after each working step.

---

## 12. Limitations and next steps

- **Confidence is self-reported and uncalibrated.** It was 0.92–0.95 on every sample ticket. A security ticket ("someone logged into my account from another country") scored 0.95 and was auto-triaged even though the model itself flagged it for review. That is the brief's rule (confidence decides), and it shows why the signal is weak. *Next:* calibrate the threshold on a larger labelled set; add deterministic overrides (e.g. high urgency plus model flag means human review); use self-consistency across samples.
- **Small evaluation set:** one ticket moves accuracy by about 17 points. *Next:* a larger labelled set with ambiguous and multi-issue tickets.
- **No label descriptions or few-shot examples in the prompt.** These would likely fix billing vs technical_issue, but should be tuned on a held-out set, not the six samples.
- **Reply guardrails** are prompt rules plus a keyword guard and a sentence-count check. They can't catch every invented fact, and abbreviations like "e.g." can be miscounted as sentence breaks. *Next:* an LLM-judge check for invented facts.
- **Sequential calls with no rate-limit backoff:** fine for small batches. *Next:* backoff on 429 errors and concurrency.
- **No PII redaction** before text is sent to the provider. *Next:* mask emails, phone numbers and account numbers during preprocessing.
- **Prompt injection:** labels can't escape the schema thanks to validation, but reply text could be steered by a malicious ticket.
