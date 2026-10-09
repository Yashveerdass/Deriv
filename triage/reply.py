"""Draft customer replies (auto_triage only) and internal escalation notes (human_review)."""
import re


def build_reply_messages(cleaned_text, category, urgency):
    system = (
        "You write short draft replies for a customer support team.\n"
        f"The ticket was classified as category '{category}' with urgency '{urgency}'.\n"
        "Rules:\n"
        "- 2 to 4 sentences, plain text, no greeting line or signature.\n"
        "- Acknowledge the customer's specific issue.\n"
        "- Do NOT invent account-specific facts (balances, dates, statuses, reference numbers).\n"
        "- Do NOT promise actions, refunds or timelines that are not stated in the ticket; "
        "say the team will look into it or ask for needed details instead.\n"
        "- Tone should match the urgency."
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": f"Ticket:\n{cleaned_text}"}]


def sentence_count(text):
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])


def internal_note(route_record, prediction):
    pred = (f"model suggested {prediction['category']}/{prediction['urgency']}"
            if prediction else "no valid model prediction")
    return (f"Escalated to human review: {route_record['routing_reason']}. "
            f"{pred}. Please triage manually; no customer reply was drafted.")
