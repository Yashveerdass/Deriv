"""Offline tests for the core logic: parsing/validation, routing, metrics and the stage guard.

No network calls; run with: python -m pytest
"""
import json

import pytest

from triage.classify import parse_classification
from triage.evaluate import compute_metrics, confusion_summary
from triage.preprocess import clean_text
from triage.route import route
from triage.stages import Pipeline, Stage

SCHEMA = {"categories": ["billing", "other"], "urgency_levels": ["low", "high"]}


def make_output(**overrides):
    """A valid classification JSON string, with optional field overrides."""
    fields = {"category": "billing", "urgency": "high", "confidence": 0.9,
              "reasoning_summary": "refund question", "needs_human_review": False}
    fields.update(overrides)
    return json.dumps(fields)


# --- Schema validation and parsing ---------------------------------------------------------

def test_parses_clean_json():
    prediction, error = parse_classification(make_output(), SCHEMA)
    assert error is None and prediction["category"] == "billing"


def test_recovers_json_wrapped_in_noise():
    noisy = f"Sure, here is the result:\n```json\n{make_output()}\n```\nHope that helps!"
    prediction, error = parse_classification(noisy, SCHEMA)
    assert error is None and prediction["urgency"] == "high"


@pytest.mark.parametrize("bad_output, expected_error", [
    (make_output(category="refunds"), "invalid category"),
    (make_output(urgency="critical"), "invalid urgency"),
    (make_output(confidence=1.5), "parse/validation error"),
    ("not json at all", "parse/validation error"),
    (None, "empty model output"),
])
def test_rejects_invalid_output(bad_output, expected_error):
    prediction, error = parse_classification(bad_output, SCHEMA)
    assert prediction is None and expected_error in error


# --- Deterministic routing -------------------------------------------------------------------

@pytest.mark.parametrize("confidence, expected_route", [
    (0.64, "human_review"), (0.65, "auto_triage"), (0.99, "auto_triage"),
])
def test_routing_threshold(confidence, expected_route):
    prediction = {"confidence": confidence, "needs_human_review": False}
    assert route("T1", prediction, None)["route"] == expected_route


def test_invalid_output_goes_to_human_review():
    decision = route("T1", None, "invalid category 'refunds'")
    assert decision["route"] == "human_review" and "invalid" in decision["routing_reason"]


def test_model_flag_alone_does_not_force_review():
    # Routing is decided by code; the model's flag is recorded in the reason only.
    decision = route("T1", {"confidence": 0.9, "needs_human_review": True}, None)
    assert decision["route"] == "auto_triage" and "model also flagged" in decision["routing_reason"]


# --- Metrics -----------------------------------------------------------------------------------

def test_metrics_and_confusion():
    rows = [
        {"expected_category": "billing", "predicted_category": "billing", "category_correct": True,
         "urgency_correct": True, "route": "auto_triage", "parse_error": None, "retried": False},
        {"expected_category": "billing", "predicted_category": "other", "category_correct": False,
         "urgency_correct": False, "route": "auto_triage", "parse_error": None, "retried": False},
        {"expected_category": "other", "predicted_category": None, "category_correct": False,
         "urgency_correct": None, "route": "human_review", "parse_error": "bad json", "retried": True},
    ]
    metrics = compute_metrics(rows)
    assert metrics["category_accuracy"] == round(1 / 3, 4)
    assert metrics["urgency_accuracy"] == 0.5  # unlabelled urgency is excluded, not counted wrong
    assert metrics["human_review_count"] == 1
    assert metrics["parse_validation_failures"] == 1
    mistakes = confusion_summary(rows)["most_confused"]
    assert {"expected": "billing", "predicted": "other", "count": 1} in mistakes


# --- Preprocessing and stage guard -----------------------------------------------------------

def test_clean_text_is_deterministic():
    assert clean_text("  Help!!!   why??  ") == "Help! why?"


def test_stage_guard_rejects_skipped_stage():
    pipeline = Pipeline()
    pipeline.advance(Stage.INPUTS_LOADED)
    with pytest.raises(RuntimeError):
        pipeline.advance(Stage.ROUTED)


def _pipeline_at_routed():
    pipeline = Pipeline()
    for stage in list(Stage)[1:list(Stage).index(Stage.ROUTED) + 1]:
        pipeline.advance(stage)
    return pipeline


def test_response_stage_cannot_be_skipped_when_tickets_were_auto_triaged():
    pipeline = _pipeline_at_routed()
    pipeline.response_optional = False  # at least one auto_triage ticket
    with pytest.raises(RuntimeError):
        pipeline.advance(Stage.RESULTS_SAVED)


def test_response_stage_can_be_skipped_when_everything_was_escalated():
    pipeline = _pipeline_at_routed()
    pipeline.response_optional = True  # every ticket went to human_review
    pipeline.advance(Stage.RESULTS_SAVED)
    assert pipeline.stage == Stage.RESULTS_SAVED
