"""Input loading and deterministic text preprocessing."""
import json
import re


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_inputs(tickets_path, schema_path):
    """Load and sanity-check both input files. Raises ValueError with a readable message."""
    tickets = load_json(tickets_path)
    schema = load_json(schema_path)

    if not isinstance(tickets, list) or not tickets:
        raise ValueError(f"{tickets_path} must be a non-empty JSON list")
    seen_ids = set()
    for index, ticket in enumerate(tickets):
        if not isinstance(ticket, dict):
            raise ValueError(f"{tickets_path}: entry {index} is not a JSON object")
        if "ticket_id" not in ticket or "customer_message" not in ticket:
            raise ValueError(f"{tickets_path}: entry {index} is missing ticket_id or customer_message")
        if not isinstance(ticket["customer_message"], str):
            raise ValueError(f"{tickets_path}: ticket {ticket['ticket_id']} customer_message is not a string")
        ticket_id = str(ticket["ticket_id"])
        if ticket_id in seen_ids:
            raise ValueError(f"{tickets_path}: duplicate ticket_id {ticket_id}")
        seen_ids.add(ticket_id)

    if not isinstance(schema, dict):
        raise ValueError(f"{schema_path} must be a JSON object")
    for key in ("categories", "urgency_levels"):
        labels = schema.get(key)
        if not isinstance(labels, list) or not labels or not all(isinstance(l, str) for l in labels):
            raise ValueError(f"{schema_path}: '{key}' must be a non-empty list of strings")
    return tickets, schema


def clean_text(text):
    text = re.sub(r"\s+", " ", str(text)).strip()
    text = re.sub(r"([!?.])\1+", r"\1", text)  # "!!!" -> "!", "???" -> "?"
    return text


def preprocess(tickets):
    out = []
    for t in tickets:
        cleaned = clean_text(t["customer_message"])
        out.append({
            "ticket_id": str(t["ticket_id"]),
            "original_text": t["customer_message"],
            "cleaned_text": cleaned,
            "char_count": len(cleaned),
            "word_count": len(cleaned.split()),
        })
    return out
