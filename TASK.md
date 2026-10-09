## BUILD

Build a small, replayable AI-powered ticket triage pipeline that reads customer support tickets from local files, classifies each ticket into a category and urgency level, produces a short suggested reply, and routes low-confidence cases to human review.

This is not just a prompt demo. The evaluator will run your solution from a clean checkout, may replace the input files with equivalent fixtures, and will verify that your pipeline loads data, applies deterministic preprocessing, makes structured model calls, enforces confidence-based routing, saves outputs, and reports basic evaluation metrics.

Your implementation should show practical AI engineering judgment for an entry-level engineer: clear structure, reliable parsing, reproducible outputs, and simple validation.

---

## INPUT FILES

Your pipeline must read from disk:

- `tickets.json`
- `label_schema.json`

You may also use an optional config file if helpful.

---

## SAMPLE `label_schema.json`

```json
{
  "categories": [
    "billing",
    "verification",
    "login_access",
    "technical_issue",
    "account_closure",
    "feature_request",
    "other"
  ],
  "urgency_levels": [
    "low",
    "medium",
    "high"
  ]
}
```

---

## SAMPLE `tickets.json`

```json
[
  {
    "ticket_id": "T1",
    "customer_message": "I updated my password yesterday but I still can't log in to my account. The reset email works, but after setting a new password it says invalid credentials.",
    "expected_category": "login_access",
    "expected_urgency": "high"
  },
  {
    "ticket_id": "T2",
    "customer_message": "I withdrew funds two days ago and still don't see them in my bank account. Can you check what's happening?",
    "expected_category": "billing",
    "expected_urgency": "high"
  },
  {
    "ticket_id": "T3",
    "customer_message": "Your mobile site is slow on my phone and the chart sometimes freezes when I switch tabs.",
    "expected_category": "technical_issue",
    "expected_urgency": "medium"
  },
  {
    "ticket_id": "T4",
    "customer_message": "What documents do I need to verify my account if my address changed recently?",
    "expected_category": "verification",
    "expected_urgency": "medium"
  },
  {
    "ticket_id": "T5",
    "customer_message": "Please close my account permanently. I no longer want to use this service.",
    "expected_category": "account_closure",
    "expected_urgency": "medium"
  },
  {
    "ticket_id": "T6",
    "customer_message": "It would be great if dark mode was available in the dashboard.",
    "expected_category": "feature_request",
    "expected_urgency": "low"
  }
]
```

The evaluator may replace these with similar tickets and labels. Your implementation must not depend on exact wording.

---

## PIPELINE STAGES

Your implementation must enforce these stages in code:

```text
INIT
 -> INPUTS_LOADED
 -> TEXT_PREPROCESSED
 -> MODEL_PROMPTED
 -> STRUCTURED_OUTPUT_PARSED
 -> CONFIDENCE_CHECKED
 -> ROUTED
 -> RESPONSE_GENERATED, if not human-review only
 -> RESULTS_SAVED
 -> EVALUATION_COMPUTED
 -> VALIDATION_COMPLETED
```

---

## MUST COMPLETE

### 1. Input Loading and Preprocessing

Load all tickets and label schema from disk.

Apply simple deterministic preprocessing to each ticket, such as:

- whitespace cleanup
- length stats
- optional normalization for repeated punctuation or casing

Save a machine-readable preprocessed file to `preprocessed_tickets.json`.

Each record must include at least:

```json
{
  "ticket_id": "string",
  "original_text": "string",
  "cleaned_text": "string",
  "char_count": 0,
  "word_count": 0
}
```

---

### 2. Structured Classification Call

For each ticket, make an LLM call that returns structured JSON with:

```json
{
  "category": "one of the allowed categories",
  "urgency": "one of the allowed urgency levels",
  "confidence": 0.0,
  "reasoning_summary": "short explanation",
  "needs_human_review": false
}
```

Requirements:

- the prompt must include the allowed labels from `label_schema.json`
- the model must be instructed to return valid JSON only
- your code must parse and validate the response
- invalid category or urgency values must be rejected and handled in code

Save raw model outputs and parsed predictions.

---

### 3. Deterministic Confidence Routing

Do not rely only on the model's `needs_human_review` field.

Implement deterministic routing logic in code:

- if `confidence < 0.65`, route to `human_review`
- otherwise route to `auto_triage`

If the model output is invalid or unparsable, route to `human_review` with an error reason.

Save routing decisions to `routing_decisions.json`.

Each record must include:

```json
{
  "ticket_id": "string",
  "route": "auto_triage | human_review",
  "confidence": 0.0,
  "routing_reason": "string"
}
```

---

### 4. Suggested Reply Generation

For tickets routed to `auto_triage`, make a second LLM call that generates a short draft support reply.

The reply must:

- be 2-4 sentences
- acknowledge the issue
- avoid making up account-specific facts
- be consistent with the predicted category and urgency
- avoid promising actions that are not stated in the ticket

For tickets routed to `human_review`, do not generate a normal customer reply. Instead produce an internal note explaining why the case was escalated.

Save outputs to `triage_results.json`.

Each record must include:

```json
{
  "ticket_id": "string",
  "predicted_category": "string",
  "predicted_urgency": "string",
  "confidence": 0.0,
  "route": "string",
  "customer_reply": "string | null",
  "internal_note": "string | null"
}
```

---

### 5. Basic Evaluation Report

Use the `expected_category` and `expected_urgency` fields in `tickets.json` to compute basic evaluation metrics:

- category accuracy
- urgency accuracy
- number of tickets sent to human review
- number of parse/validation failures

Also save a per-ticket comparison report.

Save outputs to:

- `evaluation_report.json`
- `prediction_comparison.json`

---

### 6. LLM Call Logging

Create `llm_calls.jsonl` with one JSON object per LLM call.

Each record must include:

```json
{
  "stage": "classification | reply_generation",
  "ticket_id": "string",
  "timestamp": "ISO-8601 timestamp",
  "provider": "string",
  "model": "string",
  "prompt_hash": "string",
  "output_artifact": "path"
}
```

---

### 7. Validation Command

Include a validation command, for example `python validate.py` or `make validate`.

The validation command must check that:

- required artifacts exist
- JSON files are valid
- all tickets received a routing decision
- all `auto_triage` tickets have a customer reply
- all `human_review` tickets have an internal note
- predicted labels belong to the allowed schema
- evaluation metrics can be computed

---

## SHOULD ATTEMPT

### 8. Prompt/Output Robustness

Add one recovery path for malformed classification output, such as:

- extracting the first JSON object from noisy text
- retrying once with a stricter prompt
- logging parse failure details clearly

This should be visible in code and artifacts.

### 9. Lightweight Test Coverage

Add a few small tests for core logic such as:

- schema validation
- routing threshold behavior
- metric calculation

Use any simple Python test framework.

### 10. Simple CLI

Add a small CLI so an operator can run something like:

```bash
python main.py --tickets tickets.json --schema label_schema.json
```

Optional flags for output directory or model settings are welcome.

---

## STRETCH

### 11. Confusion Summary

Produce a simple confusion summary for category predictions showing which labels were most often mixed up. Save to `confusion_summary.json`.

---

## REQUIRED ARTIFACTS

- `tickets.json`
- `label_schema.json`
- `preprocessed_tickets.json`
- `routing_decisions.json`
- `triage_results.json`
- `prediction_comparison.json`
- `evaluation_report.json`
- `llm_calls.jsonl`

If attempted: test files, `confusion_summary.json`

---

## EXECUTION REQUIREMENTS

- The evaluator will run the solution from a clean checkout.
- Generated artifacts may be deleted before evaluation.
- The evaluator may replace the input ticket file with equivalent fixtures using the same structure.
- Static precomputed outputs are not sufficient. Your code must actually run the pipeline and regenerate outputs.

## TOOLS

Python is preferred. Any LLM provider or local model may be used. You may use common libraries for validation, prompting, and CLI handling.

## TECHNICAL CONSTRAINTS

- Input must come from local JSON files.
- Classification must use structured output with schema validation.
- Routing to human review must be decided by deterministic code, not by the LLM alone.
- Invalid or malformed model outputs must not crash the pipeline.
- Replies must only be generated for auto-triaged tickets.
- The repository must include a runnable validation command.
