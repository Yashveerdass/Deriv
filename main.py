"""CLI entry point: python main.py --tickets tickets.json --schema label_schema.json"""
import argparse
import json
import os

from triage.preprocess import load_inputs, preprocess
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
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    p = Pipeline()

    tickets, schema = load_inputs(args.tickets, args.schema)
    p.advance(Stage.INPUTS_LOADED)
    print(f"Loaded {len(tickets)} tickets, {len(schema['categories'])} categories")

    pre = preprocess(tickets)
    save_json(args.out, "preprocessed_tickets.json", pre)
    p.advance(Stage.TEXT_PREPROCESSED)

    save_json(args.out, "pipeline_run.json", p.history)
    print(f"Stage: {p.stage.value}. Artifacts in {args.out}/")


if __name__ == "__main__":
    main()
