"""Offline end-to-end tests of the orchestration in main.py, using a scripted FakeLLM.

These guard the behaviour the unit tests can't: the strict retry, escalation, and
"replies are only generated for auto-triaged tickets".
"""
import json

import pytest

from tests.fake_llm import classification, run_pipeline
from validate import validate

TICKETS = [{"ticket_id": tid, "customer_message": f"Ticket {tid} text."}
           for tid in ("RETRY_OK", "GARBAGE", "LOW_CONF", "REPLY_FAILS", "ALL_GOOD")]

SCRIPT = {
    "RETRY_OK": [("not json", None), (classification("billing", "high", 0.9), None)],
    "GARBAGE": [("still not json", None), ("nope", None)],
    "LOW_CONF": [(classification("other", "low", 0.3), None)],
    "REPLY_FAILS": [(classification("technical_issue", "medium", 0.9), None)],
    "ALL_GOOD": [(classification("login_access", "high", 0.95), None)],
}
REPLIES = {"REPLY_FAILS": (None, "APITimeoutError: timed out")}


@pytest.fixture
def run(tmp_path, monkeypatch):
    out_dir, fake = run_pipeline(tmp_path, monkeypatch, TICKETS, SCRIPT, REPLIES)
    load = lambda name: json.loads((out_dir / name).read_text(encoding="utf-8"))
    results = {r["ticket_id"]: r for r in load("triage_results.json")}
    routes = {d["ticket_id"]: d for d in load("routing_decisions.json")}
    return {"out": out_dir, "fake": fake, "results": results, "routes": routes, "load": load,
            "paths": (out_dir, tmp_path / "tickets.json", tmp_path / "schema.json")}


def test_garbage_then_valid_retries_once_and_is_auto_triaged(run):
    classification_calls = [c for c in run["fake"].calls if c == ("classification", "RETRY_OK")]
    assert len(classification_calls) == 2
    assert run["results"]["RETRY_OK"]["route"] == "auto_triage"
    assert run["results"]["RETRY_OK"]["customer_reply"]


def test_garbage_twice_goes_to_human_review_with_note(run):
    result = run["results"]["GARBAGE"]
    assert result["route"] == "human_review"
    assert result["internal_note"] and result["customer_reply"] is None
    assert "invalid model output" in run["routes"]["GARBAGE"]["routing_reason"]


def test_low_confidence_gets_no_reply_call(run):
    assert run["results"]["LOW_CONF"]["route"] == "human_review"
    assert ("reply_generation", "LOW_CONF") not in run["fake"].calls
    assert ("reply_generation", "GARBAGE") not in run["fake"].calls


def test_reply_api_error_escalates_with_clear_reason(run):
    assert run["results"]["REPLY_FAILS"]["route"] == "human_review"
    assert run["results"]["REPLY_FAILS"]["customer_reply"] is None
    assert run["routes"]["REPLY_FAILS"]["routing_reason"].startswith("escalated after reply check")


def test_run_records_all_stages_and_validates(run):
    pipeline_run = run["load"]("pipeline_run.json")
    stages = [entry["stage"] for entry in pipeline_run["stages"]]
    assert stages[-1] == "VALIDATION_COMPLETED" and "RESPONSE_GENERATED" in stages
    assert pipeline_run["validation_passed"] is True
    assert validate(*map(str, run["paths"])) == []
    metrics = run["load"]("evaluation_report.json")["metrics"]
    assert metrics["parse_validation_failures"] == 1 and metrics["human_review_count"] == 3


def test_all_escalated_skips_response_stage(tmp_path, monkeypatch):
    tickets = [{"ticket_id": "X", "customer_message": "??"}]
    out_dir, fake = run_pipeline(tmp_path, monkeypatch, tickets,
                                 {"X": [(classification("other", "low", 0.2), None)]})
    stages = [e["stage"] for e in json.loads((out_dir / "pipeline_run.json").read_text())["stages"]]
    assert "RESPONSE_GENERATED" not in stages and stages[-1] == "VALIDATION_COMPLETED"
    assert not any(stage == "reply_generation" for stage, _ in fake.calls)
