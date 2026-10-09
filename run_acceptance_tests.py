"""Acceptance tests: exercise the whole pipeline the way an evaluator would, in one command.

Run:   .venv\\Scripts\\python run_acceptance_tests.py
- Works in a temp folder on a fresh `git clone` of the committed code; never touches outputs/ or .env.
- Makes ~40 live LLM calls (Groq free tier) for the positive/edge groups. The API key is read
  from .env only to pass it to the pipeline subprocess; it is never printed.
- Every test is labelled POSITIVE (happy path), NEGATIVE (bad input must fail cleanly),
  EDGE (unusual but valid input) or ABNORMAL (broken model output / tampered artifacts).
Exit code 0 only if every test passes.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
PY = sys.executable
WORK = Path(tempfile.mkdtemp(prefix="triage_acceptance_"))
CLONE = WORK / "clone"
THRESHOLD = 0.65
STAGES = ["INIT", "INPUTS_LOADED", "TEXT_PREPROCESSED", "MODEL_PROMPTED", "STRUCTURED_OUTPUT_PARSED",
          "CONFIDENCE_CHECKED", "ROUTED", "RESPONSE_GENERATED", "RESULTS_SAVED",
          "EVALUATION_COMPUTED", "VALIDATION_COMPLETED"]
REQUIRED = ["preprocessed_tickets.json", "predictions.json", "routing_decisions.json", "triage_results.json",
            "prediction_comparison.json", "evaluation_report.json", "confusion_summary.json",
            "llm_calls.jsonl", "pipeline_run.json"]

KEY = dotenv_values(ROOT / ".env").get("GROQ_API_KEY") or os.environ.get("GROQ_API_KEY")
BASE_ENV = {k: v for k, v in os.environ.items() if k not in ("GROQ_API_KEY", "LLM_BASE_URL", "LLM_MODEL")}
ENV_KEY = {**BASE_ENV, "GROQ_API_KEY": KEY or ""}
ENV_NOKEY = dict(BASE_ENV)
RESULTS = []


class Skip(Exception):
    pass


# ----------------------------------------------------------------------------- helpers

def redact(text):
    return text.replace(KEY, "***") if KEY else text


def sh(*args, env=None, cwd=None, timeout=900):
    """Run `python <args>` and return (exit code, combined output)."""
    proc = subprocess.run([PY, *map(str, args)], cwd=cwd or CLONE, env=env or ENV_KEY,
                          capture_output=True, timeout=timeout)
    return proc.returncode, redact((proc.stdout + proc.stderr).decode("utf-8", "replace"))


def tail(text, n=6):
    return "\n".join(text.strip().splitlines()[-n:])


def J(path):
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()] if path.suffix == ".jsonl" else json.load(f)


def W(path, data):
    path = Path(path)
    with open(path, "w", encoding="utf-8") as f:
        if path.suffix == ".jsonl":
            f.writelines(json.dumps(d, ensure_ascii=False) + "\n" for d in data)
        else:
            json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def artifact_path(p):
    p = Path(p)
    return p if p.is_absolute() else CLONE / p


def expect_clean_error(code, out, needle):
    assert code == 1, f"exit code {code}, expected 1\n{tail(out)}"
    assert "Traceback" not in out, f"raw traceback instead of a clean error:\n{tail(out)}"
    assert needle in out, f"expected '{needle}' in output:\n{tail(out)}"
    return next((line.strip() for line in out.splitlines() if "ERROR" in line), "")


def expect_validation_fails(out_dir, tickets, needle):
    code, out = sh("validate.py", "--out", out_dir, "--tickets", tickets, "--schema", CLONE / "label_schema.json")
    assert code == 1, f"validate.py exit {code}, expected 1 (tampering NOT detected)\n{tail(out)}"
    assert "Traceback" not in out, f"validate.py crashed instead of reporting:\n{tail(out)}"
    assert needle in out, f"expected '{needle}' in validate output:\n{tail(out)}"
    return next((line.strip() for line in out.splitlines() if line.startswith("FAIL")), "")


def run_test(test_id, kind, title, what, fn):
    start = time.time()
    try:
        detail, status = fn() or "", "PASS"
    except Skip as exc:
        detail, status = str(exc), "SKIP"
    except AssertionError as exc:
        detail, status = str(exc), "FAIL"
    except Exception as exc:  # a test that crashes is a failed test, not a crashed runner
        detail, status = f"{type(exc).__name__}: {exc}", "FAIL"
    RESULTS.append((test_id, kind, title, status))
    print(f"[{status}] {test_id:<4} {kind:<9} {title}  ({time.time() - start:.1f}s)")
    print(f"       tests: {what}")
    if detail:
        for line in str(detail).splitlines()[:8]:
            print(f"       -> {line}")


def section(name):
    print(f"\n{'=' * 78}\n {name}\n{'=' * 78}")


# ----------------------------------------------------------------------------- fixtures

SWAP_TICKETS = [
    {"ticket_id": "S1", "customer_message": "hi", "expected_category": "other", "expected_urgency": "low"},
    {"ticket_id": "S2", "customer_message": "I was charged twice for my subscription this month and the app "
                                            "also crashes when I open settings.",
     "expected_category": "billing", "expected_urgency": "medium"},
    {"ticket_id": "S3", "customer_message": "   Can you add a CSV export button???   \n\n  Thanks!!!"},
    {"ticket_id": "S4", "customer_message": "Mi cuenta está bloqueada 😡 y no puedo iniciar sesión desde ayer.",
     "expected_category": "login_access", "expected_urgency": "high"},
    {"ticket_id": "S5", "customer_message": ("My withdrawal from last week still has not arrived in my bank "
                                             "account. " * 40).strip(),
     "expected_category": "billing", "expected_urgency": "high"},
    {"ticket_id": 7, "customer_message": "Please delete my account and all my data.",
     "expected_category": "account_closure", "expected_urgency": "medium"},
]
ALT_SCHEMA = {"categories": ["payments", "access", "bug", "feedback", "other"], "urgency_levels": ["p3", "p2", "p1"]}
ALT_TICKETS = [
    {"ticket_id": "A1", "customer_message": "I reset my password but still can't log in."},
    {"ticket_id": "A2", "customer_message": "My withdrawal hasn't reached my bank after two days."},
    {"ticket_id": "A3", "customer_message": "Dark mode in the dashboard would be great."},
]


# ----------------------------------------------------------------------------- tests

def unit_tests():
    from triage.classify import parse_classification
    from triage.reply import reply_problem
    from triage.route import route
    from triage.stages import Pipeline, Stage
    schema = {"categories": ["billing", "other"], "urgency_levels": ["low", "high"]}
    good = '{"category":"billing","urgency":"high","confidence":0.9,"reasoning_summary":"x","needs_human_review":false}'

    def u1():
        at, below = route("T", {"confidence": 0.65}, None)["route"], route("T", {"confidence": 0.6499}, None)["route"]
        assert (at, below) == ("auto_triage", "human_review"), f"0.65 -> {at}, 0.6499 -> {below}"
        return "0.65 -> auto_triage, 0.6499 -> human_review"
    run_test("U1", "EDGE", "Routing threshold boundary",
             "deterministic routing rule: confidence < 0.65 -> human_review, exactly 0.65 -> auto_triage", u1)

    def u2():
        p = Pipeline()
        for s in list(Stage)[1:7]:
            p.advance(s)
        p.response_optional = False
        try:
            p.advance(Stage.RESULTS_SAVED)
        except RuntimeError as exc:
            return f"rejected: {exc}"
        raise AssertionError("ROUTED -> RESULTS_SAVED was allowed although tickets were auto-triaged")
    run_test("U2", "NEGATIVE", "Stage guard blocks an illegal skip",
             "stages enforced in code: RESPONSE_GENERATED cannot be skipped when replies are needed", u2)

    def u3():
        problems = {r: reply_problem(r) for r in ("We’ve logged your request. A specialist will review it.",
                                                  "We will refund you today. Thanks for waiting.")}
        assert all(problems.values()), f"commitment not caught: {problems}"
        return "; ".join(problems.values())
    run_test("U3", "EDGE", "Reply guard catches commitments (curly apostrophe too)",
             "reply safety: replies that promise or claim actions are blocked in code", u3)

    def u4():
        one, five = reply_problem("Thanks."), reply_problem("A. B. C. D. E.")
        ok = reply_problem("Thanks for reporting this. A specialist will review your ticket.")
        assert one and five and ok is None, f"1 sentence: {one} | 5 sentences: {five} | 2 sentences: {ok}"
        return f"1 sentence -> {one}; 5 sentences -> {five}; 2 sentences -> accepted"
    run_test("U4", "EDGE", "Reply length rule (2-4 sentences)", "reply rule from the brief enforced in code", u4)

    def u5():
        cases = {"prose + code fence": f"Sure!\n```json\n{good}\n```", "truncated JSON": good[:40],
                 "UPPERCASE label": good.replace("billing", "BILLING"), "not JSON": "I can't help with that"}
        out = {name: parse_classification(text, schema) for name, text in cases.items()}
        assert out["prose + code fence"][0] is not None, "JSON inside prose was not recovered"
        for name in ("truncated JSON", "UPPERCASE label", "not JSON"):
            assert out[name][0] is None, f"{name} was accepted"
        return "recovered: prose+fence | rejected without crashing: truncated, UPPERCASE label, plain text"
    run_test("U5", "ABNORMAL", "Malformed model output is recovered or rejected",
             "structured-output parsing + recovery path (extract first JSON object, schema check)", u5)


def negative_tests():
    bad = WORK / "bad"
    bad.mkdir()
    schema = CLONE / "label_schema.json"
    cases = [
        ("C1", "Missing tickets file", "input loading: nonexistent file", None, "could not load inputs"),
        ("C2", "Invalid JSON in tickets file", "input loading: unparsable JSON", "{not json", "could not load inputs"),
        ("C3", "Ticket without customer_message", "input validation: required field missing",
         [{"ticket_id": "T1"}], "missing ticket_id or customer_message"),
        ("C4", "Duplicate ticket_id", "input validation: IDs must be unique",
         [{"ticket_id": "T1", "customer_message": "a"}, {"ticket_id": "T1", "customer_message": "b"}], "duplicate"),
        ("C5", "Empty ticket list", "input validation: nothing to process", [], "non-empty"),
        ("C6", "customer_message is a number", "input validation: wrong type",
         [{"ticket_id": "T1", "customer_message": 42}], "not a string"),
    ]
    for test_id, title, what, content, needle in cases:
        def t(content=content, test_id=test_id, needle=needle):
            path = bad / f"{test_id}.json"
            if content is not None:
                path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
            code, out = sh("main.py", "--tickets", path, "--schema", schema, "--out", bad / f"out_{test_id}")
            return expect_clean_error(code, out, needle)
        run_test(test_id, "NEGATIVE", title, what + " -> clean ERROR, exit 1, no traceback", t)

    def c7():
        path = W(bad / "schema_empty.json", {"categories": [], "urgency_levels": ["low"]})
        code, out = sh("main.py", "--tickets", CLONE / "tickets.json", "--schema", path, "--out", bad / "out_c7")
        return expect_clean_error(code, out, "non-empty list")
    run_test("C7", "NEGATIVE", "Schema with no categories",
             "label schema validation -> clean ERROR, exit 1", c7)

    def c8():
        out_dir = WORK / "out_nokey"
        code, out = sh("main.py", "--out", out_dir, env=ENV_NOKEY)
        msg = expect_clean_error(code, out, "GROQ_API_KEY is not set")
        assert not out_dir.exists(), "pipeline started running before failing"
        return msg + " (failed before any stage ran)"
    run_test("C8", "NEGATIVE", "No API key on a fresh clone",
             "fail fast: a missing key stops the run instead of producing an all-escalated 'green' run", c8)

    def c9():
        tickets = W(bad / "two.json", [{"ticket_id": "D1", "customer_message": "I can't log in."},
                                       {"ticket_id": "D2", "customer_message": "Where is my withdrawal?"}])
        env = {**ENV_NOKEY, "GROQ_API_KEY": "dummy-key-for-test", "LLM_BASE_URL": "http://127.0.0.1:9/v1"}
        code, out = sh("main.py", "--tickets", tickets, "--out", bad / "out_dead", env=env)
        return expect_clean_error(code, out, "all LLM calls failed")
    run_test("C9", "ABNORMAL", "LLM provider unreachable",
             "provider outage / bad endpoint: every call fails -> run aborts with exit 1, no crash", c9)


def positive_tests():
    out = CLONE / "outputs"

    def a1():
        code, text = sh("-m", "pytest", "-q")
        assert code == 0, tail(text)
        return tail(text, 1)
    run_test("A1", "POSITIVE", "Unit tests pass on a fresh clone", "pytest suite from a clean checkout", a1)

    def a2():
        if not KEY:
            raise Skip("no GROQ_API_KEY in .env")
        code, text = sh("main.py", "--tickets", "tickets.json", "--schema", "label_schema.json")
        assert code == 0 and "Validation passed" in text and "Traceback" not in text, tail(text)
        missing = [name for name in REQUIRED if not (out / name).exists()]
        assert not missing, f"missing artifacts: {missing}"
        return next((line for line in text.splitlines() if line.startswith("Metrics")), "")[:200]
    run_test("A2", "POSITIVE", "Clean checkout: full live run on sample tickets",
             "whole pipeline regenerates every required artifact from scratch (evaluator's path)", a2)

    def a3():
        code, text = sh("validate.py")
        assert code == 0, tail(text)
        return tail(text, 1)
    run_test("A3", "POSITIVE", "validate.py passes", "the brief's validation command on fresh outputs", a3)

    def a4():
        stages = [s["stage"] for s in J(out / "pipeline_run.json")["stages"]]
        auto = any(r["route"] == "auto_triage" for r in J(out / "triage_results.json"))
        expected = STAGES if auto else [s for s in STAGES if s != "RESPONSE_GENERATED"]
        assert stages == expected, f"got {stages}"
        return " -> ".join(stages)
    run_test("A4", "POSITIVE", "Stages ran in the exact required order",
             "stage enforcement: pipeline_run.json history matches the brief's stage list", a4)

    def a5():
        need = {"preprocessed_tickets.json": ["ticket_id", "original_text", "cleaned_text", "char_count", "word_count"],
                "routing_decisions.json": ["ticket_id", "route", "confidence", "routing_reason"],
                "triage_results.json": ["ticket_id", "predicted_category", "predicted_urgency", "confidence",
                                        "route", "customer_reply", "internal_note"],
                "llm_calls.jsonl": ["stage", "ticket_id", "timestamp", "provider", "model", "prompt_hash",
                                    "output_artifact"]}
        problems = [f"{f}: {k}" for f, keys in need.items() for rec in J(out / f) for k in keys if k not in rec]
        for rec in J(out / "predictions.json"):
            if rec["prediction"]:
                problems += [f"prediction {rec['ticket_id']}: {k}" for k in
                             ("category", "urgency", "confidence", "reasoning_summary", "needs_human_review")
                             if k not in rec["prediction"]]
        assert not problems, f"missing fields: {problems[:5]}"
        return "all record schemas match the brief (4 artifacts + classification JSON)"
    run_test("A5", "POSITIVE", "Every artifact has the brief's required fields",
             "output schemas for sections 1, 2, 3, 4 and 6 of the brief", a5)

    def a6():
        messages = {str(t["ticket_id"]): t["customer_message"] for t in J(CLONE / "tickets.json")}
        for r in J(out / "preprocessed_tickets.json"):
            c = r["cleaned_text"]
            assert r["original_text"] == messages[r["ticket_id"]], f"{r['ticket_id']}: original_text altered"
            assert c == " ".join(c.split()), f"{r['ticket_id']}: whitespace not cleaned"
            assert r["char_count"] == len(c) and r["word_count"] == len(c.split()), f"{r['ticket_id']}: wrong counts"
        return "original kept, whitespace normalised, char/word counts correct"
    run_test("A6", "POSITIVE", "Preprocessing is correct", "deterministic preprocessing + length stats", a6)

    def a7():
        preds = {p["ticket_id"]: p["prediction"] for p in J(out / "predictions.json")}
        notes = []
        for d in J(out / "routing_decisions.json"):
            p = preds[d["ticket_id"]]
            if d["routing_reason"].startswith("escalated after reply check"):
                assert d["route"] == "human_review", d
                notes.append(f"{d['ticket_id']} escalated by reply check")
                continue
            expected = "human_review" if p is None or p["confidence"] < THRESHOLD else "auto_triage"
            assert d["route"] == expected, f"{d['ticket_id']}: route {d['route']}, rule says {expected}"
        return "every route matches the rule in code" + (f" ({'; '.join(notes)})" if notes else "")
    run_test("A7", "POSITIVE", "Routing follows the deterministic rule",
             "confidence < 0.65 or invalid -> human_review, else auto_triage (not the model's flag)", a7)

    def a8():
        from triage.reply import reply_problem
        example = ""
        for r in J(out / "triage_results.json"):
            if r["route"] == "auto_triage":
                assert r["customer_reply"] and r["internal_note"] is None, f"{r['ticket_id']}: bad auto record"
                problem = reply_problem(r["customer_reply"])
                assert problem is None, f"{r['ticket_id']}: {problem}"
                example = example or f"{r['ticket_id']}: {r['customer_reply']}"
            else:
                assert r["customer_reply"] is None and r["internal_note"], f"{r['ticket_id']}: bad human record"
        return example[:240]
    run_test("A8", "POSITIVE", "Replies only for auto-triage, notes only for human review",
             "reply generation rules: 2-4 sentences, no commitments; escalations get an internal note", a8)

    def a9():
        calls = J(out / "llm_calls.jsonl")
        preds = {p["ticket_id"]: p["prediction"] for p in J(out / "predictions.json")}
        for c in calls:
            datetime.fromisoformat(c["timestamp"])
            assert artifact_path(c["output_artifact"]).exists(), f"missing raw file {c['output_artifact']}"
            assert c["stage"] in ("classification", "reply_generation") and c["prompt_hash"], c
            if c["stage"] == "reply_generation":
                p = preds[c["ticket_id"]]
                assert p and p["confidence"] >= THRESHOLD, f"reply call for non-auto ticket {c['ticket_id']}"
        missing = set(preds) - {c["ticket_id"] for c in calls if c["stage"] == "classification"}
        assert not missing, f"no classification call logged for {missing}"
        stages = [c["stage"] for c in calls]
        return (f"{len(calls)} calls: {stages.count('classification')} classification, "
                f"{stages.count('reply_generation')} reply; every raw file exists")
    run_test("A9", "POSITIVE", "LLM call log is complete and traceable",
             "llm_calls.jsonl: one line per call, ISO timestamps, raw output files exist, replies only for auto", a9)

    def a10():
        tickets = J(CLONE / "tickets.json")
        results = {r["ticket_id"]: r for r in J(out / "triage_results.json")}
        m = J(out / "evaluation_report.json")["metrics"]
        acc = {}
        for field in ("category", "urgency"):
            flags = [results[str(t["ticket_id"])][f"predicted_{field}"] == t[f"expected_{field}"]
                     for t in tickets if f"expected_{field}" in t]
            acc[field] = round(sum(flags) / len(flags), 4)
            assert m[f"{field}_accuracy"] == acc[field], f"{field}: report {m[f'{field}_accuracy']} != {acc[field]}"
        human = sum(r["route"] == "human_review" for r in results.values())
        assert m["human_review_count"] == human, "human_review_count mismatch"
        matrix = J(out / "confusion_summary.json")["matrix"]
        assert sum(sum(v.values()) for v in matrix.values()) == len(tickets), "confusion matrix total mismatch"
        return (f"category {acc['category']}, urgency {acc['urgency']}, human review {human}, "
                f"parse failures {m['parse_validation_failures']} (all recomputed independently)")
    run_test("A10", "POSITIVE", "Metrics and confusion summary are correct",
             "evaluation report recomputed from tickets.json expected labels vs triage_results", a10)

    def a11():
        before = J(out / "triage_results.json")
        code, text = sh("main.py", "--replay", env=ENV_NOKEY)
        assert code == 0, tail(text)
        assert J(out / "triage_results.json") == before, "replayed results differ from the live run"
        calls = J(out / "llm_calls.jsonl")
        assert all(c.get("replayed") for c in calls), "some calls were not replayed"
        return f"identical results, {len(calls)} calls replayed, no API key needed"
    run_test("A11", "POSITIVE", "Replay reproduces the run offline",
             "reproducibility: --replay with NO key gives byte-identical triage_results", a11)


def edge_tests():
    out = WORK / "out_swap"
    tickets = W(WORK / "swap_tickets.json", SWAP_TICKETS)

    def b1():
        if not KEY:
            raise Skip("no GROQ_API_KEY in .env")
        code, text = sh("main.py", "--tickets", tickets, "--out", out)
        assert code == 0 and "Validation passed" in text, tail(text)
        code, text = sh("validate.py", "--tickets", tickets, "--out", out)
        assert code == 0, tail(text)
        routes = {r["ticket_id"]: r["route"] for r in J(out / "triage_results.json")}
        return "routes: " + ", ".join(f"{k}={v}" for k, v in routes.items())
    run_test("B1", "EDGE", "Swapped fixtures run with no code change",
             "evaluator replaces tickets.json: one-word, messy, unlabelled, Spanish+emoji, very long, numeric ID", b1)

    def b2():
        results = J(out / "triage_results.json")
        reply_ids = {c["ticket_id"] for c in J(out / "llm_calls.jsonl") if c["stage"] == "reply_generation"}
        reasons = {d["ticket_id"]: d["routing_reason"] for d in J(out / "routing_decisions.json")}
        human = [r for r in results if r["route"] == "human_review"]
        assert human, "no ticket went to human review (model gave every swapped ticket confidence >= 0.65)"
        for r in human:
            assert r["internal_note"] and r["customer_reply"] is None, f"{r['ticket_id']}: bad escalation record"
            if r["ticket_id"] in reply_ids:
                assert reasons[r["ticket_id"]].startswith("escalated after reply check"), \
                    f"{r['ticket_id']}: reply drafted for a low-confidence ticket"
        return "; ".join(f"{r['ticket_id']}: {reasons[r['ticket_id']][:70]}" for r in human)
    run_test("B2", "EDGE", "Ambiguous tickets are escalated, with no reply drafted",
             "human-review path end to end: low confidence -> internal note, no reply LLM call", b2)

    def b3():
        m = J(out / "evaluation_report.json")["metrics"]
        labelled = sum("expected_category" in t for t in SWAP_TICKETS)
        assert m["labelled_for_category"] == labelled and m["total_tickets"] == len(SWAP_TICKETS), m
        return f"{m['total_tickets']} tickets processed, accuracy over the {labelled} labelled ones"
    run_test("B3", "EDGE", "Unlabelled ticket excluded from accuracy",
             "evaluation copes with fixtures missing expected labels", b3)

    def b4():
        cleaned = {r["ticket_id"]: r for r in J(out / "preprocessed_tickets.json")}
        assert cleaned["S3"]["cleaned_text"] == "Can you add a CSV export button? Thanks!", cleaned["S3"]["cleaned_text"]
        assert cleaned["S5"]["char_count"] > 2000, "long ticket truncated or lost"
        assert "😡" in cleaned["S4"]["cleaned_text"] and "está" in cleaned["S4"]["cleaned_text"], "unicode mangled"
        assert "7" in cleaned, "numeric ticket_id not normalised to a string"
        return ("'   ...button???   Thanks!!!' -> 'Can you add a CSV export button? Thanks!'; "
                f"long ticket {cleaned['S5']['char_count']} chars kept; emoji/accents kept; ticket_id 7 -> '7'")
    run_test("B4", "EDGE", "Preprocessing handles messy, long, unicode and numeric-ID input",
             "deterministic cleaning on unusual text", b4)

    def b5():
        if not KEY:
            raise Skip("no GROQ_API_KEY in .env")
        alt_out = WORK / "out_alt"
        schema, alt = W(WORK / "alt_schema.json", ALT_SCHEMA), W(WORK / "alt_tickets.json", ALT_TICKETS)
        code, text = sh("main.py", "--tickets", alt, "--schema", schema, "--out", alt_out)
        assert code == 0, tail(text)
        labels = [(r["predicted_category"], r["predicted_urgency"]) for r in J(alt_out / "triage_results.json")
                  if r["predicted_category"]]
        bad = [lab for lab in labels if lab[0] not in ALT_SCHEMA["categories"]
               or lab[1] not in ALT_SCHEMA["urgency_levels"]]
        assert not bad, f"labels outside the new schema: {bad}"
        return f"predicted with the new label set: {labels}"
    run_test("B5", "EDGE", "Completely different label schema",
             "labels are read from label_schema.json, not hard-coded (e.g. payments/access, p1/p2/p3)", b5)


def tamper_tests():
    src = WORK / "out_swap"
    if not (src / "triage_results.json").exists():
        src = CLONE / "outputs"
    tickets = WORK / "swap_tickets.json" if src.name == "out_swap" else CLONE / "tickets.json"
    results = J(src / "triage_results.json") if (src / "triage_results.json").exists() else []
    auto = next((r["ticket_id"] for r in results if r["route"] == "auto_triage"), None)
    human = next((r["ticket_id"] for r in results if r["route"] == "human_review"), None)

    def tampered(mutate, needle, needs=()):
        def t():
            if not results:
                raise Skip("no pipeline outputs to tamper with")
            if any(x is None for x in needs):
                raise Skip("sample outputs lack an auto_triage or human_review ticket")
            d = WORK / "tamper"
            shutil.rmtree(d, ignore_errors=True)
            shutil.copytree(src, d)
            mutate(d)
            return expect_validation_fails(d, tickets, needle)
        return t

    def edit(name, fn):
        def m(d):
            data = J(d / name)
            fn(data)
            W(d / name, data)
        return m

    def rec(tid):
        return lambda data: next(r for r in data if r["ticket_id"] == tid)

    def fake_reply_call(d):
        calls = J(d / "llm_calls.jsonl")
        calls.append({**calls[0], "stage": "reply_generation", "ticket_id": human})
        W(d / "llm_calls.jsonl", calls)

    cases = [
        ("D1", "Auto ticket's reply deleted", edit("triage_results.json", lambda x: rec(auto)(x).update(customer_reply=None)),
         "auto_triage without customer_reply", (auto,)),
        ("D2", "Human ticket's internal note deleted", edit("triage_results.json", lambda x: rec(human)(x).update(internal_note=None)),
         "human_review without internal_note", (human,)),
        ("D3", "Reply added to a human-review ticket", edit("triage_results.json", lambda x: rec(human)(x).update(customer_reply="Hi.")),
         "has a customer_reply", (human,)),
        ("D4", "Predicted label not in schema", edit("triage_results.json", lambda x: rec(auto)(x).update(predicted_category="payments")),
         "not in schema", (auto,)),
        ("D5", "Auto-triaged with confidence 0.30", edit("triage_results.json", lambda x: rec(auto)(x).update(confidence=0.3)),
         "confidence", (auto,)),
        ("D6", "Route differs between routing and results", edit("routing_decisions.json", lambda x: rec(auto)(x).update(route="human_review")),
         "differs", (auto,)),
        ("D7", "Reply LLM call logged for an escalated ticket", fake_reply_call, "not auto-triaged", (human,)),
        ("D8", "A ticket missing from triage_results", edit("triage_results.json", lambda x: x.pop()), "FAIL", ()),
        ("D9", "LLM log line missing required fields", lambda d: W(d / "llm_calls.jsonl", J(d / "llm_calls.jsonl") + [{"stage": "classification"}]),
         "FAIL", ()),
        ("D10", "Result record missing its 'route' key", edit("triage_results.json", lambda x: x[0].pop("route")), "FAIL", ()),
        ("D11", "Corrupted JSON artifact", lambda d: (d / "evaluation_report.json").write_text("{oops", encoding="utf-8"),
         "invalid JSON", ()),
        ("D12", "Required artifact deleted", lambda d: (d / "llm_calls.jsonl").unlink(), "missing artifact", ()),
        ("D13", "Metric in the report edited", edit("evaluation_report.json", lambda x: x["metrics"].update(category_accuracy=0.01)),
         "metric", ()),
    ]
    for test_id, title, mutate, needle, needs in cases:
        run_test(test_id, "ABNORMAL", title, "validate.py detects a broken/tampered artifact -> FAIL, exit 1, no crash",
                 tampered(mutate, needle, needs))


# ----------------------------------------------------------------------------- main

def main():
    started = time.time()
    print(f"Acceptance tests for the ticket triage pipeline\nworkspace: {WORK}")
    print(f"API key found in .env: {'yes' if KEY else 'NO - live tests will be skipped'} (value never shown)")
    proc = subprocess.run(["git", "clone", "-q", str(ROOT), str(CLONE)], capture_output=True, text=True)
    if proc.returncode:
        print(f"git clone failed: {proc.stderr}")
        sys.exit(1)
    head = subprocess.run(["git", "-C", str(CLONE), "log", "-1", "--format=%h %s"], capture_output=True, text=True)
    print(f"testing committed code at: {head.stdout.strip()}  (uncommitted changes are NOT included)")

    section("U. CORE LOGIC (in-process, no network)")
    unit_tests()
    section("C. NEGATIVE: bad inputs and bad environment must fail cleanly")
    negative_tests()
    section("A. POSITIVE: clean checkout, full live run (the evaluator's path)")
    positive_tests()
    section("B. EDGE: swapped fixtures and unusual input")
    edge_tests()
    section("D. ABNORMAL: tampered artifacts must be caught by validate.py")
    tamper_tests()

    section("SUMMARY")
    for kind in ("POSITIVE", "NEGATIVE", "EDGE", "ABNORMAL"):
        rows = [r for r in RESULTS if r[1] == kind]
        passed = sum(r[3] == "PASS" for r in rows)
        print(f"  {kind:<9} {passed}/{len(rows)} passed")
    failed = [r for r in RESULTS if r[3] == "FAIL"]
    skipped = [r for r in RESULTS if r[3] == "SKIP"]
    total_pass = sum(r[3] == "PASS" for r in RESULTS)
    print(f"\n  TOTAL     {total_pass}/{len(RESULTS)} passed, {len(failed)} failed, {len(skipped)} skipped "
          f"({time.time() - started:.0f}s)")
    for test_id, kind, title, _ in failed:
        print(f"  FAILED: {test_id} [{kind}] {title}")
    if failed or skipped:
        print(f"\n  Workspace kept for inspection: {WORK}")
    else:
        shutil.rmtree(WORK, ignore_errors=True)
    print("\n  RESULT: " + ("ALL TESTS PASSED" if not failed and not skipped else "SEE FAILURES ABOVE"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
