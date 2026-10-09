"""LLM client wrapper: one place for provider config, raw-output saving, call logging and replay."""
import hashlib
import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-20b"


def prompt_hash(model, messages):
    payload = json.dumps({"model": model, "messages": messages}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class LLM:
    def __init__(self, out_dir, model=None, replay=False, timeout=30):
        self.out_dir = out_dir
        self.raw_dir = os.path.join(out_dir, "raw")
        os.makedirs(self.raw_dir, exist_ok=True)
        self.log_path = os.path.join(out_dir, "llm_calls.jsonl")
        self.model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        self.base_url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)
        self.provider = "groq" if "groq" in self.base_url else self.base_url
        self.replay = replay
        self.timeout = timeout
        self._client = None

    def _get_client(self):
        """Create the API client lazily, so --replay works without a key."""
        if self._client is None:
            key = os.environ.get("GROQ_API_KEY")
            if not key:
                raise RuntimeError("GROQ_API_KEY is not set (add it to .env, see .env.example)")
            self._client = OpenAI(api_key=key, base_url=self.base_url, timeout=self.timeout)
        return self._client

    def call(self, stage, ticket_id, messages, json_mode=False):
        """Returns (text or None, error or None, artifact_path). Never raises on API failure."""
        # The raw file is named by prompt hash, so an identical prompt maps to the same file (replay).
        hash_id = prompt_hash(self.model, messages)
        path = os.path.join(self.raw_dir, f"{stage}_{ticket_id}_{hash_id}.json")
        replayed = False
        if self.replay and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            text, error, replayed = saved["output"], saved["error"], True
        else:  # Live call; any failure is captured as an error string, never raised.
            text, error = None, None
            try:
                kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
                resp = self._get_client().chat.completions.create(
                    model=self.model, messages=messages, temperature=0, **kwargs)
                text = resp.choices[0].message.content
            except Exception as exc:  # network, auth, rate limit, provider-side JSON rejection
                error = f"{type(exc).__name__}: {str(exc)[:300]}"
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"stage": stage, "ticket_id": ticket_id, "prompt_hash": hash_id,
                           "model": self.model, "messages": messages,
                           "output": text, "error": error}, f, indent=2, ensure_ascii=False)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "stage": stage, "ticket_id": ticket_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "provider": self.provider, "model": self.model,
                "prompt_hash": hash_id, "output_artifact": path.replace(os.sep, "/"),
                "replayed": replayed, "error": error,
            }) + "\n")
        return text, error, path
