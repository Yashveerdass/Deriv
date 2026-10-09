"""Classification prompt, robust parsing and schema validation."""
import json

from pydantic import BaseModel, Field, ValidationError


class Classification(BaseModel):
    category: str
    urgency: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_summary: str
    needs_human_review: bool


def build_messages(cleaned_text, schema, strict=False):
    system = (
        "You are a customer support triage classifier. Classify the ticket.\n"
        f"Allowed categories: {json.dumps(schema['categories'])}\n"
        f"Allowed urgency levels: {json.dumps(schema['urgency_levels'])}\n"
        "Return valid JSON only, with exactly these keys:\n"
        '{"category": <allowed category>, "urgency": <allowed urgency>, '
        '"confidence": <number 0.0-1.0>, "reasoning_summary": <one short sentence>, '
        '"needs_human_review": <true|false>}\n'
        "Set confidence low if the ticket is ambiguous or fits no category well."
    )
    if strict:
        system += ("\nYOUR PREVIOUS ANSWER WAS INVALID. Output ONLY the JSON object, no prose, "
                   "no markdown. category and urgency MUST be copied exactly from the allowed lists.")
    return [{"role": "system", "content": system},
            {"role": "user", "content": f"Ticket:\n{cleaned_text}"}]


def extract_json(text):
    """json.loads, falling back to the first decodable {...} object inside noisy text."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text, start)
            return obj
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
    raise ValueError("no JSON object found in model output")


def parse_classification(text, schema):
    """Returns (prediction dict, None) or (None, error string). Never raises."""
    if text is None:
        return None, "empty model output"
    try:
        obj = extract_json(text)
        if not isinstance(obj, dict):
            return None, "model output JSON is not an object"
        pred = Classification(**obj).model_dump()
    except (ValueError, ValidationError) as e:
        return None, f"parse/validation error: {str(e)[:300]}"
    if pred["category"] not in schema["categories"]:
        return None, f"invalid category '{pred['category']}'"
    if pred["urgency"] not in schema["urgency_levels"]:
        return None, f"invalid urgency '{pred['urgency']}'"
    return pred, None
