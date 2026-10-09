"""Pipeline stages, in order, with a guard that rejects skipped or out-of-order steps."""
from datetime import datetime, timezone
from enum import Enum


class Stage(str, Enum):
    INIT = "INIT"
    INPUTS_LOADED = "INPUTS_LOADED"
    TEXT_PREPROCESSED = "TEXT_PREPROCESSED"
    MODEL_PROMPTED = "MODEL_PROMPTED"
    STRUCTURED_OUTPUT_PARSED = "STRUCTURED_OUTPUT_PARSED"
    CONFIDENCE_CHECKED = "CONFIDENCE_CHECKED"
    ROUTED = "ROUTED"
    RESPONSE_GENERATED = "RESPONSE_GENERATED"
    RESULTS_SAVED = "RESULTS_SAVED"
    EVALUATION_COMPUTED = "EVALUATION_COMPUTED"
    VALIDATION_COMPLETED = "VALIDATION_COMPLETED"


ORDER = list(Stage)


class Pipeline:
    def __init__(self):
        self.stage = Stage.INIT
        self.history = [{"stage": Stage.INIT.value, "at": _now()}]

    def advance(self, nxt: Stage):
        allowed = {ORDER[ORDER.index(self.stage) + 1]}
        # RESPONSE_GENERATED is skipped when every ticket went to human review.
        if self.stage == Stage.ROUTED:
            allowed.add(Stage.RESULTS_SAVED)
        if nxt not in allowed:
            raise RuntimeError(f"Illegal stage transition {self.stage.value} -> {nxt.value}")
        self.stage = nxt
        self.history.append({"stage": nxt.value, "at": _now()})


def _now():
    return datetime.now(timezone.utc).isoformat()
