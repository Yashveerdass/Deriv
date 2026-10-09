"""Deterministic confidence routing. The model's needs_human_review flag is recorded but never trusted alone."""
CONFIDENCE_THRESHOLD = 0.65


def route(ticket_id, prediction, error, threshold=CONFIDENCE_THRESHOLD):
    """Pure function: same prediction in, same routing decision out."""
    # Unparsable or schema-invalid output can never be auto-triaged.
    if prediction is None:
        return {"ticket_id": ticket_id, "route": "human_review", "confidence": 0.0,
                "routing_reason": f"invalid model output: {error}"}
    confidence = prediction["confidence"]
    if confidence < threshold:
        route_name = "human_review"
        reason = f"confidence {confidence:.2f} < threshold {threshold}"
    else:
        route_name = "auto_triage"
        reason = f"confidence {confidence:.2f} >= threshold {threshold}"
    if prediction.get("needs_human_review"):
        reason += " (model also flagged needs_human_review)"
    return {"ticket_id": ticket_id, "route": route_name, "confidence": confidence, "routing_reason": reason}
