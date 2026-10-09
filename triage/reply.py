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
        "- NEVER state or imply that any action has been taken or will be taken: no 'we will...', "
        "'we'll...', 'we've logged/escalated/locked...', no timelines, no refunds.\n"
        "- NEVER state company policy, required documents, fees or eligibility rules.\n"
        "- You MAY say the request will be reviewed by the appropriate team (passive voice), "
        "and you MAY ask for details that would help.\n"
        "- Tone should match the urgency."
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": f"Ticket:\n{cleaned_text}"}]


# Code-side safety net for the prompt rules above: first-person commitments or claimed actions.
# Matches both straight (') and curly (’) apostrophes, since models emit either.
COMMITMENT_PATTERN = re.compile(r"\bwe(?:['’]ve|['’]ll| have| will)\b|\bimmediately\b|\brefund",
                                re.IGNORECASE)


def sentence_count(text):
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])


def reply_problem(reply):
    """Return why a draft reply must not be sent, or None if it passes the code checks."""
    if not reply:
        return "empty reply"
    if not 2 <= sentence_count(reply) <= 4:
        return f"reply has {sentence_count(reply)} sentences, expected 2-4"
    match = COMMITMENT_PATTERN.search(reply)
    if match:
        return f"reply made a commitment ('{match.group(0)}')"
    return None


def internal_note(route_record, prediction):
    pred = (f"model suggested {prediction['category']}/{prediction['urgency']}"
            if prediction else "no valid model prediction")
    return (f"Escalated to human review: {route_record['routing_reason']}. "
            f"{pred}. Please triage manually; no customer reply was drafted.")
