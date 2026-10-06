"""AgentCore Platform v1.0"""

# Classification confidence as the downstream steps read it.
#
# The confidence score is produced inside the pipeline, but three steps compare
# it against a threshold, and each comparison is the exact decision the
# template exists for: escalate the severity, require a human review, accept
# the part. A NaN compares False against every threshold, so an unchecked
# value would silently take the permissive branch of every one of them — no
# escalation, no review, and an ACCEPT disposition at "nan%" confidence.
#
# One reader, used by every step: anything that is not a finite number in
# [0, 1] is read as 0.0 — fully uncertain — so every decision fails CLOSED.

import math
from typing import Any

CONFIDENCE_MIN = 0.0
CONFIDENCE_MAX = 1.0


def finite_confidence(value: Any) -> float:
    """Return a confidence in [0, 1]; anything else reads as 0.0 (uncertain)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return CONFIDENCE_MIN
    parsed = float(value)
    if not math.isfinite(parsed) or not (CONFIDENCE_MIN <= parsed <= CONFIDENCE_MAX):
        return CONFIDENCE_MIN
    return parsed
