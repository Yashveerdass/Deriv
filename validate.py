"""Independent checks on the pipeline's artifacts.

Usage: python validate.py [--out outputs] [--tickets tickets.json] [--schema label_schema.json]
Exits 1 and lists every failed check if anything is wrong. Never crashes on a malformed
artifact: a missing field is reported as a failure, not a traceback.
"""
import argparse
import json
import os
import sys

from triage.evaluate import compute_metrics
from triage.route import CONFIDENCE_THRESHOLD

REQUIRED_OUTPUTS = [
    "preprocessed_tickets.json", "predictions.json", "routing_decisions.json",
    "triage_results.json", "prediction_comparison.json", "evaluation_report.json",
    "llm_calls.jsonl", "confusion_summary.json",
]
LOG_FIELDS = ["stage", "ticket_id", "timestamp", "provider", "model", "prompt_hash", "output_artifact"]
LOG_STAGES = {"classification", "reply_generation"}
ROUTES = {"auto_triage", "human_review"}


def _load(path):
    """Parse a .json or .jsonl file; raises on invalid JSON."""
    with open(path, encoding="utf-8") as f:
        if path.endswith(".jsonl"):
            return [json.loads(line) for line in f if line.strip()]
        return json.load(f)


def _per_record(records, check, label_field="ticket_id"):
    """Run check(record) on each record; turn a missing field into a readable failure."""
    failures = []
    for index, record in enumerate(records):
        name = record.get(label_field, f"record {index}") if isinstance(record, dict) else f"record {index}"
        try:
            failures += check(record)
        except KeyError as exc:
            failures.append(f"{name}: missing field {exc}")
        except (TypeError, AttributeError) as exc:
            failures.append(f"{name}: malformed record ({exc})")
    return failures


def _one_per_ticket(records, ticket_ids, artifact):
    """Check 3: exactly one record per ticket in an artifact."""
    ids = sorted(str(r.get("ticket_id")) for r in records if isinstance(r, dict))
    return [] if ids == ticket_ids else [f"{artifact}: ticket ids {ids} != tickets {ticket_ids}"]


def check_result(result, schema, routed_by_id):
    """Checks 4-6 on one triage result, plus consistency with deterministic routing."""
    failures = []
    ticket_id, route, confidence = result["ticket_id"], result["route"], result["confidence"]
    if route not in ROUTES:
        return [f"{ticket_id}: unknown route '{route}'"]
    if routed_by_id.get(ticket_id) != route:
        failures.append(f"{ticket_id}: route '{route}' differs from routing_decisions "
                        f"'{routed_by_id.get(ticket_id)}'")
    if route == "auto_triage":
        if not result["customer_reply"]:
            failures.append(f"{ticket_id}: auto_triage without customer_reply")
        if confidence < CONFIDENCE_THRESHOLD:
            failures.append(f"{ticket_id}: auto_triage with confidence {confidence} < {CONFIDENCE_THRESHOLD}")
    else:
        if not result["internal_note"]:
            failures.append(f"{ticket_id}: human_review without internal_note")
        if result["customer_reply"]:
            failures.append(f"{ticket_id}: human_review ticket has a customer_reply")
    # Labels must be in the schema. None is allowed only on escalated tickets with invalid model output.
    for field, allowed in (("predicted_category", schema["categories"]),
                           ("predicted_urgency", schema["urgency_levels"])):
        value = result[field]
        if value is None and route == "auto_triage":
            failures.append(f"{ticket_id}: auto_triage ticket missing {field}")
        elif value is not None and value not in allowed:
            failures.append(f"{ticket_id}: {field} '{value}' not in schema")
    return failures


def check_log_entry(entry, out_dir, prediction_by_id):
    """Each llm_calls.jsonl line is complete, points at a real artifact, and replies only follow
    a valid prediction at or above the threshold ("replies only for auto-triaged tickets")."""
    failures = []
    missing = [field for field in LOG_FIELDS if field not in entry]
    if missing:
        return [f"llm_calls.jsonl entry for {entry.get('ticket_id')}: missing fields {missing}"]
    ticket_id = entry["ticket_id"]
    if entry["stage"] not in LOG_STAGES:
        failures.append(f"{ticket_id}: unknown log stage '{entry['stage']}'")
    artifact = entry["output_artifact"]
    if not (os.path.exists(artifact) or os.path.exists(os.path.join(out_dir, "raw", os.path.basename(artifact)))):
        failures.append(f"{ticket_id}: logged output_artifact not found: {artifact}")
    if entry["stage"] == "reply_generation":
        prediction = (prediction_by_id.get(ticket_id) or {}).get("prediction")
        if not prediction or prediction["confidence"] < CONFIDENCE_THRESHOLD:
            failures.append(f"{ticket_id}: reply_generation call for a ticket that was not auto-triaged")
    return failures


def check_metrics(data):
    """Check 7: metrics recompute from the comparison and match the saved report."""
    recomputed = compute_metrics(data["prediction_comparison.json"])
    report = data["evaluation_report.json"]["metrics"]
    return [f"metric {key}: report {report.get(key)} != recomputed {value}"
            for key, value in recomputed.items() if report.get(key) != value]


def validate(out_dir="outputs", tickets_path="tickets.json", schema_path="label_schema.json"):
    """Return a list of failure messages (an empty list means every check passed)."""
    failures = []

    # Checks 1-2: required artifacts exist and are valid JSON.
    data = {}
    for path in [tickets_path, schema_path] + [os.path.join(out_dir, name) for name in REQUIRED_OUTPUTS]:
        if not os.path.exists(path):
            failures.append(f"missing artifact: {path}")
            continue
        try:
            data[os.path.basename(path)] = _load(path)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            failures.append(f"invalid JSON in {path}: {exc}")
    if failures:
        return failures  # the remaining checks need every file

    schema = data[os.path.basename(schema_path)]
    ticket_ids = sorted(str(t.get("ticket_id")) for t in data[os.path.basename(tickets_path)])
    routing_decisions = data["routing_decisions.json"]
    triage_results = data["triage_results.json"]
    routed_by_id = {d.get("ticket_id"): d.get("route") for d in routing_decisions if isinstance(d, dict)}
    prediction_by_id = {p.get("ticket_id"): p for p in data["predictions.json"] if isinstance(p, dict)}

    # Check 3: every ticket has exactly one routing decision and one triage result.
    failures += _one_per_ticket(routing_decisions, ticket_ids, "routing_decisions.json")
    failures += _one_per_ticket(triage_results, ticket_ids, "triage_results.json")
    failures += _per_record(routing_decisions, lambda d: (
        [] if d["route"] in ROUTES else [f"{d['ticket_id']}: unknown route '{d['route']}'"]))

    # Checks 4-6, plus route consistency and the confidence threshold.
    failures += _per_record(triage_results, lambda r: check_result(r, schema, routed_by_id))

    # Call log: complete entries, real artifacts, replies only for auto-triaged tickets.
    failures += _per_record(data["llm_calls.jsonl"],
                            lambda e: check_log_entry(e, out_dir, prediction_by_id))

    # Check 7: metrics can be computed and match the report.
    try:
        failures += check_metrics(data)
    except (KeyError, TypeError, AttributeError) as exc:
        failures.append(f"metrics could not be computed: {exc!r}")

    return failures


def main():
    parser = argparse.ArgumentParser(description="Validate triage pipeline artifacts")
    parser.add_argument("--out", default="outputs")
    parser.add_argument("--tickets", default="tickets.json")
    parser.add_argument("--schema", default="label_schema.json")
    args = parser.parse_args()

    failures = validate(args.out, args.tickets, args.schema)
    for failure in failures:
        print(f"FAIL: {failure}")
    print("VALIDATION PASSED" if not failures else f"VALIDATION FAILED ({len(failures)} issues)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
