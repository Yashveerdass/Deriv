"""Tamper tests: start from a good outputs folder, corrupt one thing, and check that
validate() reports it (as a failure message, never an exception)."""
import json
import os

import pytest

from tests.fake_llm import classification, run_pipeline
from validate import validate

TICKETS = [
    {"ticket_id": "A", "customer_message": "I can't log in.", "expected_category": "login_access",
     "expected_urgency": "high"},
    {"ticket_id": "B", "customer_message": "Something is wrong??", "expected_category": "other",
     "expected_urgency": "low"},
]
SCRIPT = {"A": [(classification("login_access", "high", 0.9), None)],   # auto_triage
          "B": [(classification("other", "low", 0.3), None)]}           # human_review


@pytest.fixture
def good_run(tmp_path, monkeypatch):
    out_dir, _ = run_pipeline(tmp_path, monkeypatch, TICKETS, SCRIPT)
    return out_dir, str(tmp_path / "tickets.json"), str(tmp_path / "schema.json")


def _edit(out_dir, filename, mutate):
    path = os.path.join(out_dir, filename)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    mutate(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _edit_log(out_dir, mutate):
    path = os.path.join(out_dir, "llm_calls.jsonl")
    with open(path, encoding="utf-8") as f:
        entries = [json.loads(line) for line in f]
    mutate(entries)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(entry) + "\n" for entry in entries)


def test_good_run_passes(good_run):
    assert validate(*good_run) == []


def _reply_entry_for_b(entries):
    """Pretend a reply was generated for B, which was routed to human_review."""
    entries.append(dict(entries[0], stage="reply_generation", ticket_id="B"))


TAMPERS = {
    "missing artifact": lambda out: os.remove(os.path.join(out, "predictions.json")),
    "invalid json": lambda out: open(os.path.join(out, "triage_results.json"), "w").write("{oops"),
    "routing missing a ticket": lambda out: _edit(out, "routing_decisions.json", lambda d: d.pop()),
    "results missing a ticket": lambda out: _edit(out, "triage_results.json", lambda d: d.pop()),
    "auto without reply": lambda out: _edit(out, "triage_results.json",
                                            lambda d: d[0].update(customer_reply=None)),
    "human_review without note": lambda out: _edit(out, "triage_results.json",
                                                   lambda d: d[1].update(internal_note=None)),
    "label outside schema": lambda out: _edit(out, "triage_results.json",
                                              lambda d: d[0].update(predicted_category="refunds")),
    "auto below threshold": lambda out: _edit(out, "triage_results.json",
                                              lambda d: d[0].update(confidence=0.3)),
    "routes disagree": lambda out: _edit(out, "routing_decisions.json",
                                         lambda d: d[0].update(route="human_review")),
    "reply call for review ticket": lambda out: _edit_log(out, _reply_entry_for_b),
    "log line missing fields": lambda out: _edit_log(out, lambda e: e[0].pop("prompt_hash")),
    "log artifact missing": lambda out: _edit_log(out, lambda e: e[0].update(output_artifact="nope.json")),
    "record missing route": lambda out: _edit(out, "triage_results.json", lambda d: d[0].pop("route")),
    "metrics inconsistent": lambda out: _edit(out, "evaluation_report.json",
                                              lambda d: d["metrics"].update(category_accuracy=0.0)),
}


@pytest.mark.parametrize("name", TAMPERS)
def test_tampering_is_detected(good_run, name):
    out_dir = good_run[0]
    TAMPERS[name](str(out_dir))
    failures = validate(*good_run)  # must return failures, never raise
    assert failures, f"tamper '{name}' was not detected"
