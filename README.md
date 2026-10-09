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

## Outputs (`outputs/`)
`preprocessed_tickets.json`, `predictions.json` (parsed predictions plus every attempt), `raw/*.json` (raw model I/O, named by prompt hash), `routing_decisions.json`, `triage_results.json`, `prediction_comparison.json`, `evaluation_report.json`, `confusion_summary.json`, `llm_calls.jsonl`, `pipeline_run.json` (stage history).

## Approach and tradeoffs
- **Stages are enforced.** `triage/stages.py` raises an error on any out-of-order transition. RESPONSE_GENERATED is skipped only when every ticket was escalated.
- **Labels come only from the schema file.** Nothing is hard-coded, so swapped fixtures work.
- **Structured output:** JSON mode, `temperature=0`, pydantic validation (confidence 0-1) and a membership check against the schema. If parsing fails, the pipeline (1) extracts the first `{...}` from noisy text, then (2) retries once with a stricter prompt. Every attempt and its error is recorded in `predictions.json`.
- **Routing is a pure function:** confidence `< 0.65` or invalid output goes to `human_review`. The model's `needs_human_review` flag is recorded in the reason but never decides the route on its own.
- **Reply safety:** the prompt forbids invented account facts and unstated promises. A reply that fails or isn't 2-4 sentences is never sent; the ticket is escalated instead.
- **Internal notes are templates, not LLM calls.** They are deterministic, cost nothing, and can't leak a customer-facing reply.
- **Failures never crash the run.** API errors become error records and the ticket is routed to human review.
- **Provider:** Groq (`openai/gpt-oss-20b`) via the `openai` SDK, with a 30s timeout. Any OpenAI-compatible endpoint works through `LLM_BASE_URL` / `LLM_MODEL`.
- **Tradeoff:** model self-reported confidence isn't calibrated. On the sample data every ticket scored 0.92 or higher, so the threshold rarely triggers.

## Results on the sample tickets
Category accuracy 0.83, urgency accuracy 0.67, 0 human review, 0 parse failures. The main confusion is billing -> technical_issue (a missing withdrawal labelled as a technical fault).

## How I used AI
Claude Code was my pair programmer. I wrote the spec and the build order first, then built it slice by slice. I reviewed each slice and ran it (live and with `--replay`) before committing. Tests cover the parser, the routing boundary (0.64 vs 0.65), the metrics and the stage guard.

## Limitations and next steps
- Confidence is uncalibrated. Next step: calibrate it, or combine it with a second signal such as self-consistency across samples.
- The prompt has no label descriptions or few-shot examples. Adding them would likely fix the billing vs technical_issue confusion.
- Reply checks only cover sentence count. Next step: an LLM-judge or rule check for invented facts and promises.
- Calls are sequential, with no rate-limit backoff beyond the single retry.
