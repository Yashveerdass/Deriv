# Technical Issues: Final Review Before the Test Run

**Reviewed at:** commit `d5795bb` (all slices complete), by an independent Claude Code session that did not write the code.

**How it was tested**
- Live run on the sample tickets, then `validate.py` and `pytest`.
- A run on a **swapped fixture set**: 5 new tickets (shouty text with `!!!!`, a one-word ticket, a two-issue ticket, an unlabelled ticket).
- `--replay` determinism: two runs, outputs compared.
- **Fault injection** with a fake LLM: noisy JSON, invalid labels, garbage, API errors.
- **13 tamper tests** that corrupt artifacts on purpose to see whether `validate.py` notices.
- A **fresh `git clone`** (the evaluator's path), including a run with **no API key**.

## Summary
| # | Issue | Priority | Area |
|---|---|---|---|
| 1 | Stage guard always allows skipping RESPONSE_GENERATED | Must | `triage/stages.py`, `main.py` |
| 2 | No API key or a dead key still "passes" with exit 0 | Must | `main.py`, `triage/llm.py` |
| 3 | `main.py` exits 0 when validation fails | Must | `main.py` |
| 4 | Draft replies promise or claim actions | Must | `triage/reply.py` |
| 5 | `validate.py` misses 5 of 13 tampering cases and crashes on 1 | Must | `validate.py` |
| 6 | Misleading routing reason after a reply-check escalation | Should | `main.py` |
| 7 | Bad input files give raw tracebacks; duplicate IDs not rejected | Should | `triage/preprocess.py`, `main.py` |
| 8 | No offline test of the pipeline flow (retry, replies only for auto) | Should | `tests/` |
| 9 | `requirements.txt` not pinned | Should | `requirements.txt` |
| 10 | README claims and gaps | Should | `README.md` |

## What already works (no change needed)
- **Sample run:** every stage completes. Category accuracy 0.83, urgency 0.67, 0 parse failures. `validate.py` passes; 15/15 tests pass, including in a fresh clone.
- **Swapped fixtures:** ran end to end with no code changes. Two ambiguous tickets (confidence 0.30 / 0.40) went to `human_review` with internal notes and **no reply call**; the unlabelled ticket was excluded from accuracy.
- **Replay:** `--replay` produced an identical `triage_results.json`, with all calls marked `replayed`.
- **Parsing:** clean JSON and JSON inside prose or code fences are accepted. Invalid category or urgency, confidence 1.5, missing fields, plain text, `None` and a JSON list are all rejected. A garbage first answer followed by valid JSON on the strict retry recovers.
- **Routing:** 0.64 → `human_review`, 0.65 → `auto_triage`, invalid output → `human_review`.
- **Preprocessing:** `"   SOMEONE LOGGED IN…!!!!   …NOW???  "` → `"SOMEONE LOGGED IN…! …NOW?"`.

---

## Must fix

### 1. Stage guard always allows skipping RESPONSE_GENERATED
**Problem:** `Pipeline.advance()` always allows `ROUTED -> RESULTS_SAVED`. The brief allows skipping `RESPONSE_GENERATED` only when every ticket went to human review, and README.md already claims this is enforced.
**Evidence:** advancing `INIT…ROUTED` then `RESULTS_SAVED` succeeds even when tickets were auto-triaged.
**Fix:** add `self.response_optional = False` to `Pipeline`, and allow the skip only when it is true. In `main.py`, right after routing and **before** replies run, set `pipeline.response_optional = not any(d["route"] == "auto_triage" for d in routing_decisions)`. Advance to `RESPONSE_GENERATED` whenever reply generation ran for at least one ticket.
**Done when:** a new test shows the skip is rejected when an auto ticket exists and allowed when none do.

### 2. No API key or a dead key still "passes" with exit 0
**Problem:** in a fresh clone with no `.env`, `main.py` makes 12 failing calls, sends all 6 tickets to `human_review`, reports accuracy 0.0, prints **"Validation passed"** and **exits 0**. An evaluator whose key isn't set up sees a green run full of garbage. A revoked key (401) behaves the same way.
**Fix:**
- At startup, if not `--replay` and `GROQ_API_KEY` is unset: print `ERROR: GROQ_API_KEY is not set (copy .env.example to .env)` and exit 1 **before** any stage runs.
- After classification, if **every** ticket failed with an API error (not a parse error): save artifacts, print `ERROR: all LLM calls failed: <first error>`, and exit 1. Per-ticket failures should still degrade to human review as they do now.
**Done when:** `python main.py` in a fresh clone (no `.env`) exits 1 with that message.

### 3. `main.py` exits 0 when validation fails
**Problem:** failures from the in-process `validate()` are printed, but `VALIDATION_COMPLETED` is still recorded and the exit code is 0.
**Fix:** write `pipeline_run.json` with `{"stages": history, "validation_passed": bool, "validation_failures": [...]}`, then `sys.exit(1)` if any check failed.
**Done when:** a run whose validation fails exits non-zero.

### 4. Draft replies promise or claim actions (brief section 4)
**Evidence from live runs:**
- X1: *"We will lock your account immediately"* (a promise)
- T6: *"We've logged this feature request with our product team"* (claims an action was taken)
- T3: *"Our technical team will … work on a fix"* (a promise)
- T4: lists specific verification documents (invents policy)

**Fix (`build_reply_messages` prompt):** add these rules. Never state or imply that an action has been taken or will be taken (no "we will…", "we've logged/escalated/locked…", no timelines). Never state company policy, required documents, fees or eligibility. It may say the request will be reviewed by the right team, and may ask for details that would help. Keep the 2–4 sentence code check.
**Optional code guard:** if the reply matches `\bwe('ve| have| will|'ll)\b|immediately|refund`, escalate with reason `"escalated after reply check: reply made a commitment"`.
**Done when:** re-running the samples gives replies with no promises or claimed actions.

### 5. `validate.py` misses 5 of 13 tampering cases and crashes on 1
| Tampered artifact | Current result | Check to add |
|---|---|---|
| `triage_results.json` missing a ticket | passed | exactly one result per ticket (same as the routing check) |
| `auto_triage` with confidence 0.30 | passed | `auto_triage` ⇒ confidence ≥ 0.65 and labels present; confidence < 0.65 ⇒ `human_review`. This proves routing is deterministic |
| route differs between `routing_decisions.json` and `triage_results.json` | passed | routes agree per ticket |
| `reply_generation` call logged for a ticket with confidence < 0.65 | passed | every `reply_generation` call in `llm_calls.jsonl` is for a ticket with a valid prediction and confidence ≥ 0.65 ("replies only for auto-triaged") |
| `llm_calls.jsonl` line missing fields | passed | each line has the 7 required fields, `stage ∈ {classification, reply_generation}`, and `output_artifact` exists |
| record missing the `route` key | **KeyError traceback** | wrap the per-record checks; report `FAIL: <ticket>: missing field 'route'` |

Also add `predictions.json` to `REQUIRED_OUTPUTS`, since the brief says to save parsed predictions, and check that every `raw/` file referenced in the log exists.
**Done when:** a new test copies a good `outputs/` folder, tampers with it, and asserts `validate()` returns failures rather than raising.

---

## Should fix

### 6. Misleading routing reason after a reply-check escalation
A ticket escalated because its reply failed reads `"confidence 0.90 >= threshold 0.65; reply generation failed (…)"` while `route = human_review`.
**Fix:** start the reason with `"escalated after reply check: "`, followed by the cause and the original confidence.

### 7. Bad input files give raw tracebacks; duplicate IDs not rejected
`python main.py --tickets nope.json` gives a `FileNotFoundError` traceback.
**Fix:** in `main.py`, catch `FileNotFoundError`, `json.JSONDecodeError` and `ValueError` around `load_inputs`, print `ERROR: <message>`, and exit 1. In `load_inputs`, also reject duplicate `ticket_id`s, tickets that aren't JSON objects, non-string `customer_message`, and non-string schema labels.

### 8. No offline test of the pipeline flow
The tests cover the pure functions but not the orchestration, so nothing guards "replies only for auto-triaged" or the retry path.
**Fix:** add `tests/test_pipeline_flow.py` with a `FakeLLM` (same `call()` signature, scripted outputs) that runs `request_classifications` → `parse_classifications` → `route` → `generate_responses`, and asserts:
- garbage then valid JSON → one strict retry, then `auto_triage` with a reply;
- garbage twice → `human_review` with a note;
- confidence 0.3 → `human_review` and **no `reply_generation` call made**;
- reply API error → `human_review` with an `"escalated after reply check"` reason.

### 9. `requirements.txt` not pinned
For reproducible installs, pin the tested versions:
```
openai==3.26.1
pydantic==2.14.0
python-dotenv==1.2.4
pytest==9.1.1
```

### 10. README claims and gaps
- "RESPONSE_GENERATED is skipped only when every ticket was escalated" is true only after fix 1.
- Mention that a missing key now fails fast (fix 2), and that `--replay` falls back to a live call when no saved raw file exists.
- Link to `SUBMISSION.md` (process, design, review evidence) from the README.
- Update the "Results" line after the final run.

---

## Leave as is, but name it in the README / submission
- **Security ticket auto-triaged.** X1 ("someone logged into my account from another country") scored confidence 0.95 with `needs_human_review: true` from the model, so it went to `auto_triage`. That is exactly the brief's rule (confidence decides, not the model flag), and it shows why self-reported confidence is weak. Next step: a deterministic override such as high urgency plus model flag means human review.
- **Uncalibrated confidence:** every sample ticket scored 0.92–0.95.
- **Prompt injection:** a ticket can try to steer the model. Labels can't escape the schema because of validation, but reply text could be influenced. Fix 4's code guard partly mitigates this.
- **`sentence_count` splits on `. `**, so abbreviations like "e.g." can be miscounted. This is rare in short replies.

---

## Paste into Claude Code (VS Code)
```
Read TECHNICAL_ISSUES.md. Fix the issues in this order, one small commit each, and run pytest after each:
A) #1 stage guard + test
B) #2 fail fast on missing key / all calls failed, #3 exit 1 on validation failure, #7 clean input errors
C) #4 reply prompt (+ optional guard), #6 reason prefix
D) #5 validate.py checks + tamper test
E) #8 offline pipeline-flow test with a FakeLLM
F) #9 pin requirements, #10 README updates
Don't change the routing rule itself (confidence < 0.65 or invalid -> human_review). Stop after each commit and tell me what changed.
```

## Final test run (after all fixes)
```
Delete outputs/, then run in order and show me the output of each:
1. python main.py --tickets tickets.json --schema label_schema.json
2. python validate.py
3. python -m pytest -q
4. python main.py --replay   (then confirm triage_results.json is unchanged)
5. git clone this repo into a temp folder (it has no .env), run the .venv python main.py there, and confirm it exits 1 with a clear "GROQ_API_KEY is not set" error. Don't touch the real .env.
Then show git status, confirm .env is not staged, commit and push.
```
