"""Independent checks on the pipeline's artifacts.

Usage: python validate.py [--out outputs] [--tickets tickets.json] [--schema label_schema.json]
Exits 1 and lists every failed check if anything is wrong.
"""
import argparse
import json
import os
import sys

from triage.evaluate import compute_metrics

REQUIRED_OUTPUTS = [
    "preprocessed_tickets.json", "routing_decisions.json", "triage_results.json",
    "prediction_comparison.json", "evaluation_report.json", "llm_calls.jsonl",
    "confusion_summary.json",
]


def _load(path):
    """Parse a .json or .jsonl file; raises on invalid JSON."""
    with open(path, encoding="utf-8") as f:
        if path.endswith(".jsonl"):
            return [json.loads(line) for line in f if line.strip()]
        return json.load(f)


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

    tickets = data[os.path.basename(tickets_path)]
    schema = data[os.path.basename(schema_path)]
    routing_decisions = data["routing_decisions.json"]
    ticket_ids = sorted(str(ticket["ticket_id"]) for ticket in tickets)

    # Check 3: every ticket has exactly one routing decision with a known route.
    routed_ids = sorted(decision["ticket_id"] for decision in routing_decisions)
    if routed_ids != ticket_ids:
        failures.append(f"routing decisions {routed_ids} != tickets {ticket_ids}")
    for decision in routing_decisions:
        if decision["route"] not in ("auto_triage", "human_review"):
            failures.append(f"{decision['ticket_id']}: unknown route {decision['route']}")

    for result in data["triage_results.json"]:
        ticket_id, route = result["ticket_id"], result["route"]
        # Check 4: auto_triage tickets have a customer reply.
        if route == "auto_triage" and not result.get("customer_reply"):
            failures.append(f"{ticket_id}: auto_triage without customer_reply")
        # Check 5: human_review tickets have an internal note and no customer reply.
        if route == "human_review":
            if not result.get("internal_note"):
                failures.append(f"{ticket_id}: human_review without internal_note")
            if result.get("customer_reply"):
                failures.append(f"{ticket_id}: human_review ticket has a customer_reply")
        # Check 6: predicted labels belong to the schema.
        # None is allowed only for escalated tickets whose model output was invalid.
        for field, allowed in (("predicted_category", schema["categories"]),
                               ("predicted_urgency", schema["urgency_levels"])):
            value = result[field]
            if value is None and route != "human_review":
                failures.append(f"{ticket_id}: missing {field} on auto_triage ticket")
            elif value is not None and value not in allowed:
                failures.append(f"{ticket_id}: {field} '{value}' not in schema")

    # Check 7: metrics can be recomputed from the comparison and match the saved report.
    try:
        recomputed = compute_metrics(data["prediction_comparison.json"])
        report = data["evaluation_report.json"]["metrics"]
        for key, value in recomputed.items():
            if report.get(key) != value:
                failures.append(f"metric {key}: report {report.get(key)} != recomputed {value}")
    except (KeyError, TypeError) as exc:
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
