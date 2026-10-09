"""Evaluation: per-ticket comparison, summary metrics and a category confusion summary.

Tickets without expected labels are kept in the comparison but excluded from accuracy,
so unlabelled fixtures still run end to end.
"""
from collections import Counter


def compare(raw_tickets, predictions, triage_results):
    """One row per ticket: expected vs predicted labels, route and any parse error."""
    prediction_by_id = {record["ticket_id"]: record for record in predictions}
    result_by_id = {result["ticket_id"]: result for result in triage_results}
    rows = []
    for ticket in raw_tickets:
        ticket_id = str(ticket["ticket_id"])
        result = result_by_id[ticket_id]
        expected_category = ticket.get("expected_category")
        expected_urgency = ticket.get("expected_urgency")
        rows.append({
            "ticket_id": ticket_id,
            "expected_category": expected_category,
            "predicted_category": result["predicted_category"],
            "category_correct": (expected_category == result["predicted_category"]
                                 if expected_category is not None else None),
            "expected_urgency": expected_urgency,
            "predicted_urgency": result["predicted_urgency"],
            "urgency_correct": (expected_urgency == result["predicted_urgency"]
                                if expected_urgency is not None else None),
            "confidence": result["confidence"],
            "route": result["route"],
            "parse_error": prediction_by_id[ticket_id]["error"],
            "retried": len(prediction_by_id[ticket_id]["attempts"]) > 1,
        })
    return rows


def _accuracy(flags):
    """Share of True among non-None flags; None when nothing was labelled."""
    scored = [flag for flag in flags if flag is not None]
    return round(sum(scored) / len(scored), 4) if scored else None


def compute_metrics(comparison_rows):
    """Summary metrics. A parse failure counts as a wrong prediction, not a skipped one."""
    return {
        "total_tickets": len(comparison_rows),
        "category_accuracy": _accuracy([row["category_correct"] for row in comparison_rows]),
        "urgency_accuracy": _accuracy([row["urgency_correct"] for row in comparison_rows]),
        "labelled_for_category": sum(row["category_correct"] is not None for row in comparison_rows),
        "labelled_for_urgency": sum(row["urgency_correct"] is not None for row in comparison_rows),
        "human_review_count": sum(row["route"] == "human_review" for row in comparison_rows),
        "auto_triage_count": sum(row["route"] == "auto_triage" for row in comparison_rows),
        "parse_validation_failures": sum(row["parse_error"] is not None for row in comparison_rows),
        "classification_retries": sum(row["retried"] for row in comparison_rows),
    }


def confusion_summary(comparison_rows):
    """Counts of (expected -> predicted) category pairs, mistakes listed most frequent first."""
    pairs = Counter((row["expected_category"], row["predicted_category"] or "PARSE_FAILURE")
                    for row in comparison_rows if row["expected_category"] is not None)
    mistakes = [{"expected": expected, "predicted": predicted, "count": count}
                for (expected, predicted), count in pairs.most_common() if expected != predicted]
    matrix = {}
    for (expected, predicted), count in pairs.items():
        matrix.setdefault(expected, {})[predicted] = count
    return {"most_confused": mistakes, "matrix": matrix}
