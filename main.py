"""CLI entry point: python main.py --tickets tickets.json --schema label_schema.json"""
import argparse
import json
import os

from triage.classify import build_messages, parse_classification
from triage.llm import LLM
from triage.preprocess import load_inputs, preprocess
from triage.reply import build_reply_messages, internal_note, sentence_count
from triage.route import route
from triage.stages import Pipeline, Stage


def save_json(out_dir, name, data):
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def main():
    ap = argparse.ArgumentParser(description="AI ticket triage pipeline")
    ap.add_argument("--tickets", default="tickets.json")
    ap.add_argument("--schema", default="label_schema.json")
    ap.add_argument("--out", default="outputs")
    ap.add_argument("--model", default=None, help="override LLM_MODEL")
    ap.add_argument("--replay", action="store_true", help="reuse saved raw outputs instead of calling the API")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    p = Pipeline()

    tickets, schema = load_inputs(args.tickets, args.schema)
    p.advance(Stage.INPUTS_LOADED)
    print(f"Loaded {len(tickets)} tickets, {len(schema['categories'])} categories")

    pre = preprocess(tickets)
    save_json(args.out, "preprocessed_tickets.json", pre)
    p.advance(Stage.TEXT_PREPROCESSED)

    # Fresh call log each run (raw/ is kept so --replay can reuse it).
    if os.path.exists(os.path.join(args.out, "llm_calls.jsonl")):
        os.remove(os.path.join(args.out, "llm_calls.jsonl"))
    llm = LLM(args.out, model=args.model, replay=args.replay)

    first = {}
    for t in pre:
        text, err, path = llm.call("classification", t["ticket_id"],
                                   build_messages(t["cleaned_text"], schema), json_mode=True)
        first[t["ticket_id"]] = (text, err, path)
    p.advance(Stage.MODEL_PROMPTED)

    predictions = []
    for t in pre:
        tid = t["ticket_id"]
        text, err, path = first[tid]
        pred, perr = parse_classification(text, schema) if not err else (None, f"llm call failed: {err}")
        attempts = [{"raw_artifact": path, "error": perr}]
        if perr:  # recovery: one retry with a stricter prompt
            print(f"  {tid}: {perr} -> retrying with strict prompt")
            text, err, path = llm.call("classification", tid,
                                       build_messages(t["cleaned_text"], schema, strict=True), json_mode=True)
            pred, perr = parse_classification(text, schema) if not err else (None, f"llm call failed: {err}")
            attempts.append({"raw_artifact": path, "error": perr})
        predictions.append({"ticket_id": tid, "prediction": pred, "error": perr, "attempts": attempts})
    save_json(args.out, "predictions.json", predictions)
    p.advance(Stage.STRUCTURED_OUTPUT_PARSED)
    for r in predictions:
        print(f"  {r['ticket_id']}: {r['prediction'] or r['error']}")

    routes = [route(r["ticket_id"], r["prediction"], r["error"]) for r in predictions]
    p.advance(Stage.CONFIDENCE_CHECKED)
    save_json(args.out, "routing_decisions.json", routes)
    p.advance(Stage.ROUTED)

    text_by_id = {t["ticket_id"]: t["cleaned_text"] for t in pre}
    results = []
    for r, rt in zip(predictions, routes):
        tid, pred = r["ticket_id"], r["prediction"]
        reply = None
        if rt["route"] == "auto_triage":
            reply, err, _ = llm.call("reply_generation", tid, build_reply_messages(
                text_by_id[tid], pred["category"], pred["urgency"]))
            reply = reply.strip() if reply else None
            if not reply or not 2 <= sentence_count(reply) <= 4:
                # A missing or out-of-spec reply is never sent; escalate instead.
                rt["route"] = "human_review"
                rt["routing_reason"] += f"; reply generation failed ({err or 'reply not 2-4 sentences'})"
                reply = None
        results.append({
            "ticket_id": tid,
            "predicted_category": pred["category"] if pred else None,
            "predicted_urgency": pred["urgency"] if pred else None,
            "confidence": rt["confidence"],
            "route": rt["route"],
            "customer_reply": reply,
            "internal_note": internal_note(rt, pred) if rt["route"] == "human_review" else None,
        })
    if any(r["route"] == "auto_triage" for r in results):
        p.advance(Stage.RESPONSE_GENERATED)
    save_json(args.out, "routing_decisions.json", routes)  # re-save: may include reply-failure escalations
    save_json(args.out, "triage_results.json", results)
    p.advance(Stage.RESULTS_SAVED)

    save_json(args.out, "pipeline_run.json", p.history)
    print(f"Stage: {p.stage.value}. Artifacts in {args.out}/")


if __name__ == "__main__":
    main()
