"""Scripted stand-in for the real LLM, used by the offline pipeline tests.

It subclasses the real LLM and feeds canned outputs through the real replay path, so raw
artifacts and llm_calls.jsonl are written exactly as in a live run, with no network.
"""
import json
import os
import sys

import main
from triage.llm import LLM, prompt_hash

GOOD_REPLY = ("Thanks for reaching out about this. Your request will be reviewed by the "
              "appropriate team.", None)


def classification(category, urgency, confidence):
    """A valid classification JSON string."""
    return json.dumps({"category": category, "urgency": urgency, "confidence": confidence,
                       "reasoning_summary": "scripted", "needs_human_review": False})


class FakeLLM(LLM):
    """script: {ticket_id: [(text, api_error), ...]} consumed one per classification call.
    replies: {ticket_id: (text, api_error)}; defaults to GOOD_REPLY."""

    def __init__(self, out_dir, script, replies=None):
        super().__init__(out_dir, model="fake-model", replay=True)
        self.script = {ticket_id: list(outputs) for ticket_id, outputs in script.items()}
        self.replies = replies or {}
        self.calls = []  # (stage, ticket_id) in call order, for assertions

    def call(self, stage, ticket_id, messages, json_mode=False):
        self.calls.append((stage, ticket_id))
        if stage == "classification":
            text, error = self.script[ticket_id].pop(0)
        else:
            text, error = self.replies.get(ticket_id, GOOD_REPLY)
        # Pre-write the raw file so the real replay branch reads it and logs the call.
        raw_path = os.path.join(self.raw_dir,
                                f"{stage}_{ticket_id}_{prompt_hash(self.model, messages)}.json")
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump({"output": text, "error": error}, f)
        return super().call(stage, ticket_id, messages, json_mode)


def run_pipeline(tmp_path, monkeypatch, tickets, script, replies=None):
    """Run main.main() end to end on the given tickets with a FakeLLM. Returns (out_dir, fake)."""
    tickets_path, schema_path = tmp_path / "tickets.json", tmp_path / "schema.json"
    tickets_path.write_text(json.dumps(tickets))
    schema_path.write_text(json.dumps({
        "categories": ["billing", "login_access", "technical_issue", "other"],
        "urgency_levels": ["low", "medium", "high"],
    }))
    out_dir = tmp_path / "out"
    created = {}

    def make_fake(out, model=None, replay=False):
        created["llm"] = FakeLLM(out, script, replies)
        return created["llm"]

    monkeypatch.setattr(main, "LLM", make_fake)
    monkeypatch.setenv("GROQ_API_KEY", "not-used-by-fake")
    monkeypatch.setattr(sys, "argv", ["main.py", "--tickets", str(tickets_path),
                                      "--schema", str(schema_path), "--out", str(out_dir)])
    main.main()
    return out_dir, created["llm"]
