# Ticket Triage Pipeline

## What it does
A small, replayable AI triage pipeline. It reads support tickets (`tickets.json`) and allowed labels (`label_schema.json`), cleans the text, classifies each ticket's category and urgency with a structured LLM call, and routes low-confidence or invalid results to human review **in code**. It drafts a 2-4 sentence reply only for auto-triaged tickets and writes an internal note for escalated ones. It then saves every artifact, computes evaluation metrics and validates its own output.

## Run it
```bash
python -m venv .venv && .venv\Scripts\activate        # Windows (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
copy .env.example .env                                   # then set GROQ_API_KEY

python main.py --tickets tickets.json --schema label_schema.json   # full live run -> outputs/
python validate.py                                                  # independent artifact checks (exit 1 on failure)
python -m pytest -q                                                 # offline unit tests
python main.py --replay                                             # rerun from saved raw outputs, no API calls
```
Flags: `--out DIR` (default `outputs`), `--model NAME`, `--replay`.

Exit codes: `main.py` exits **1** if `GROQ_API_KEY` is missing (checked before any stage runs), if the input files are missing or malformed, if every LLM call fails (for example a revoked key), or if the built-in validation fails. Otherwise it exits 0. `--replay` falls back to a live call for any prompt that has no saved raw file.

Process, design notes and review evidence are in [SUBMISSION.md](SUBMISSION.md).

## Outputs (`outputs/`)
`preprocessed_tickets.json`, `predictions.json` (parsed predictions plus every attempt), `raw/*.json` (raw model I/O, named by prompt hash), `routing_decisions.json`, `triage_results.json`, `prediction_comparison.json`, `evaluation_report.json`, `confusion_summary.json`, `llm_calls.jsonl`, `pipeline_run.json` (stage history).

## Approach and tradeoffs
- **Stages are enforced.** `triage/stages.py` raises an error on any out-of-order transition. Skipping RESPONSE_GENERATED is allowed only when routing sent every ticket to human review; this is decided before replies run and covered by tests. `pipeline_run.json` records the stage history and the validation result.
- **Labels come only from the schema file.** Nothing is hard-coded, so swapped fixtures work.
- **Structured output:** JSON mode, `temperature=0`, pydantic validation (confidence 0-1) and a membership check against the schema. If parsing fails, the pipeline (1) extracts the first `{...}` from noisy text, then (2) retries once with a stricter prompt. Every attempt and its error is recorded in `predictions.json`.
- **Routing is a pure function:** confidence `< 0.65` or invalid output goes to `human_review`. The model's `needs_human_review` flag is recorded in the reason but never decides the route on its own.
- **Reply safety:** the prompt forbids invented account facts, company policy, and any claimed or promised action. A code guard (`reply_problem`) also rejects replies that aren't 2-4 sentences or that contain first-person commitments ("we will", "we've", "immediately", "refund"). A rejected reply is never sent: the ticket is escalated with the reason `escalated after reply check: ...`.
- **Validation is independent.** `validate.py` checks for 1 result and 1 routing decision per ticket, that the routes agree, that no `auto_triage` ticket is below 0.65, that replies were generated only for tickets above the threshold, that each `llm_calls.jsonl` line has every required field and points at a real raw artifact, that labels are in the schema, and that metrics recompute. A malformed record is reported, not raised.
- **Tests (48, all offline):** parsing and schema checks, the routing boundary, reply checks, metrics, input loading, the stage guard, 14 tamper cases for the validator, and end-to-end flow tests with a scripted `FakeLLM` (retry, escalation, no reply call for review tickets).
- **Internal notes are templates, not LLM calls.** They are deterministic, cost nothing, and can't leak a customer-facing reply.
- **Failures never crash the run.** API errors become error records and the ticket is routed to human review.
- **Provider:** Groq (`openai/gpt-oss-20b`) via the `openai` SDK, with a 30s timeout. Any OpenAI-compatible endpoint works through `LLM_BASE_URL` / `LLM_MODEL`.
- **Tradeoff:** model self-reported confidence isn't calibrated. On the sample data every ticket scored 0.92 or higher, so the threshold rarely triggers.

## Results on the sample tickets
Final live run (`openai/gpt-oss-20b`): category accuracy 0.83, urgency accuracy 0.67, 6/6 auto-triaged, 0 parse failures, validation passed. `--replay` reproduced `triage_results.json` byte for byte. The one category error is billing -> technical_issue (a missing withdrawal labelled as a technical fault). After the reply-prompt fix, no draft reply promises or claims an action.

## How I used AI
Claude Code was my pair programmer. I wrote the spec and the build order first, then built it slice by slice. I reviewed each slice and ran it (live and with `--replay`) before committing. Tests cover the parser, the routing boundary (0.64 vs 0.65), the metrics and the stage guard.

## Limitations and next steps
- Confidence is uncalibrated. Next step: calibrate it, or combine it with a second signal such as self-consistency across samples.
- The prompt has no label descriptions or few-shot examples. Adding them would likely fix the billing vs technical_issue confusion.
- **Security tickets can be auto-triaged.** In review, a "someone logged into my account" ticket scored 0.95 with the model's `needs_human_review: true`, and went to `auto_triage`. That follows the brief (confidence decides, not the model flag), but it shows how weak self-reported confidence is. Next step: a deterministic override, such as high urgency plus the model flag meaning human review.
- **Prompt injection:** a ticket can try to steer the model. Labels can't escape the schema, but reply text could be influenced. The commitment guard only partly mitigates this.
- The reply guard is a keyword rule. It can't catch every invented fact, and `sentence_count` can miscount abbreviations like "e.g.". Next step: an LLM-judge check.
- Calls are sequential, with no rate-limit backoff beyond the single retry.
