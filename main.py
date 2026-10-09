"""CLI entry point for the ticket triage pipeline.

Usage:
    python main.py --tickets tickets.json --schema label_schema.json [--out outputs] [--replay]

Each stage below is a small function with one job. main() only wires them together
and advances the Pipeline stage guard, so the order of stages is enforced in one place.
"""
import argparse
import json
import os

from triage.classify import build_messages, parse_classification
from triage.evaluate import compare, compute_metrics, confusion_summary
from triage.llm import LLM
from triage.preprocess import load_inputs, preprocess
from triage.reply import build_reply_messages, internal_note, sentence_count
from triage.route import route
from triage.stages import Pipeline, Stage
from validate import validate


def save_json(out_dir, filename, data):
    """Write data as pretty-printed JSON and return the file path."""
    path = os.path.join(out_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def request_classifications(llm, tickets, schema):
    """First classification attempt for every ticket. Returns {ticket_id: (text, error, artifact_path)}."""
    return {
        ticket["ticket_id"]: llm.call("classification", ticket["ticket_id"],
                                      build_messages(ticket["cleaned_text"], schema), json_mode=True)
        for ticket in tickets
    }


def _parse_llm_result(llm_result, schema):
    """Turn an (output, api_error, path) tuple into (prediction, error)."""
    output_text, api_error, _ = llm_result
    if api_error:
        return None, f"llm call failed: {api_error}"
    return parse_classification(output_text, schema)


def parse_classifications(llm, tickets, schema, first_attempts):
    """Parse and validate each first attempt, retrying once with a stricter prompt on failure."""
    predictions = []
    for ticket in tickets:
        ticket_id = ticket["ticket_id"]
        llm_result = first_attempts[ticket_id]
        prediction, error = _parse_llm_result(llm_result, schema)
        attempts = [{"raw_artifact": llm_result[2], "error": error}]

        if error:  # Recovery path: one retry with a stricter "JSON only" prompt.
            print(f"  {ticket_id}: {error} -> retrying with strict prompt")
            llm_result = llm.call("classification", ticket_id,
                                  build_messages(ticket["cleaned_text"], schema, strict=True), json_mode=True)
            prediction, error = _parse_llm_result(llm_result, schema)
            attempts.append({"raw_artifact": llm_result[2], "error": error})

        predictions.append({"ticket_id": ticket_id, "prediction": prediction,
                            "error": error, "attempts": attempts})
    return predictions


def generate_responses(llm, tickets, predictions, routing_decisions):
    """Draft a customer reply for auto_triage tickets and an internal note for human_review ones.

    A reply that fails or breaks the 2-4 sentence rule is never sent: the ticket is
    escalated instead, and its routing decision is updated to say why.
    """
    cleaned_text_by_id = {ticket["ticket_id"]: ticket["cleaned_text"] for ticket in tickets}
    triage_results = []
    for record, decision in zip(predictions, routing_decisions):
        ticket_id, prediction = record["ticket_id"], record["prediction"]
        customer_reply = None

        if decision["route"] == "auto_triage":
            reply_text, api_error, _ = llm.call("reply_generation", ticket_id, build_reply_messages(
                cleaned_text_by_id[ticket_id], prediction["category"], prediction["urgency"]))
            customer_reply = reply_text.strip() if reply_text else None
            if not customer_reply or not 2 <= sentence_count(customer_reply) <= 4:
                decision["route"] = "human_review"
                decision["routing_reason"] += (
                    f"; reply generation failed ({api_error or 'reply not 2-4 sentences'})")
                customer_reply = None

        is_escalated = decision["route"] == "human_review"
        triage_results.append({
            "ticket_id": ticket_id,
            "predicted_category": prediction["category"] if prediction else None,
            "predicted_urgency": prediction["urgency"] if prediction else None,
            "confidence": decision["confidence"],
            "route": decision["route"],
            "customer_reply": customer_reply,
            "internal_note": internal_note(decision, prediction) if is_escalated else None,
        })
    return triage_results


def parse_args():
    parser = argparse.ArgumentParser(description="AI ticket triage pipeline")
    parser.add_argument("--tickets", default="tickets.json")
    parser.add_argument("--schema", default="label_schema.json")
    parser.add_argument("--out", default="outputs", help="directory for generated artifacts")
    parser.add_argument("--model", default=None, help="override LLM_MODEL")
    parser.add_argument("--replay", action="store_true",
                        help="reuse saved raw outputs instead of calling the API")
    return parser.parse_args()


def main():
    args = parse_args()
    os.makedirs(args.out, exist_ok=True)
    pipeline = Pipeline()

    raw_tickets, schema = load_inputs(args.tickets, args.schema)
    pipeline.advance(Stage.INPUTS_LOADED)
    print(f"Loaded {len(raw_tickets)} tickets, {len(schema['categories'])} categories")

    tickets = preprocess(raw_tickets)
    save_json(args.out, "preprocessed_tickets.json", tickets)
    pipeline.advance(Stage.TEXT_PREPROCESSED)

    # Start a fresh call log each run; raw/ is kept so --replay can reuse it.
    call_log_path = os.path.join(args.out, "llm_calls.jsonl")
    if os.path.exists(call_log_path):
        os.remove(call_log_path)
    llm = LLM(args.out, model=args.model, replay=args.replay)

    first_attempts = request_classifications(llm, tickets, schema)
    pipeline.advance(Stage.MODEL_PROMPTED)

    predictions = parse_classifications(llm, tickets, schema, first_attempts)
    save_json(args.out, "predictions.json", predictions)
    pipeline.advance(Stage.STRUCTURED_OUTPUT_PARSED)
    for record in predictions:
        print(f"  {record['ticket_id']}: {record['prediction'] or record['error']}")

    routing_decisions = [route(r["ticket_id"], r["prediction"], r["error"]) for r in predictions]
    pipeline.advance(Stage.CONFIDENCE_CHECKED)
    save_json(args.out, "routing_decisions.json", routing_decisions)
    pipeline.advance(Stage.ROUTED)

    triage_results = generate_responses(llm, tickets, predictions, routing_decisions)
    if any(result["route"] == "auto_triage" for result in triage_results):
        pipeline.advance(Stage.RESPONSE_GENERATED)  # skipped when everything was escalated
    # Re-save routing: generate_responses may have escalated tickets whose reply failed.
    save_json(args.out, "routing_decisions.json", routing_decisions)
    save_json(args.out, "triage_results.json", triage_results)
    pipeline.advance(Stage.RESULTS_SAVED)

    comparison = compare(raw_tickets, predictions, triage_results)
    metrics = compute_metrics(comparison)
    save_json(args.out, "prediction_comparison.json", comparison)
    save_json(args.out, "evaluation_report.json",
              {"metrics": metrics, "provider": llm.provider, "model": llm.model})
    save_json(args.out, "confusion_summary.json", confusion_summary(comparison))
    pipeline.advance(Stage.EVALUATION_COMPUTED)
    print(f"Metrics: {json.dumps(metrics)}")

    # Same checks as `python validate.py`, run in-process so every run self-checks.
    failures = validate(args.out, args.tickets, args.schema)
    for failure in failures:
        print(f"  VALIDATION FAIL: {failure}")
    pipeline.advance(Stage.VALIDATION_COMPLETED)
    print("Validation passed" if not failures else f"Validation failed ({len(failures)} issues)")

    save_json(args.out, "pipeline_run.json", pipeline.history)
    print(f"Stage: {pipeline.stage.value}. Artifacts in {args.out}/")


if __name__ == "__main__":
    main()
