"""Input loading and deterministic text preprocessing."""
import json
import re


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_inputs(tickets_path, schema_path):
    tickets = load_json(tickets_path)
    schema = load_json(schema_path)
    if not isinstance(tickets, list) or not tickets:
        raise ValueError(f"{tickets_path} must be a non-empty JSON list")
    for t in tickets:
        if "ticket_id" not in t or "customer_message" not in t:
            raise ValueError(f"Ticket missing ticket_id/customer_message: {t}")
    if not schema.get("categories") or not schema.get("urgency_levels"):
        raise ValueError(f"{schema_path} needs non-empty categories and urgency_levels")
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
