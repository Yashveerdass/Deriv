"""Deterministic confidence routing. The model's needs_human_review flag is recorded but never trusted alone."""
CONFIDENCE_THRESHOLD = 0.65


def route(ticket_id, prediction, error, threshold=CONFIDENCE_THRESHOLD):
    if prediction is None:
        return {"ticket_id": ticket_id, "route": "human_review", "confidence": 0.0,
                "routing_reason": f"invalid model output: {error}"}
    conf = prediction["confidence"]
    if conf < threshold:
        reason = f"confidence {conf:.2f} < threshold {threshold}"
        r = "human_review"
    else:
        reason = f"confidence {conf:.2f} >= threshold {threshold}"
        r = "auto_triage"
    if prediction.get("needs_human_review"):
        reason += " (model also flagged needs_human_review)"
    return {"ticket_id": ticket_id, "route": r, "confidence": conf, "routing_reason": reason}
