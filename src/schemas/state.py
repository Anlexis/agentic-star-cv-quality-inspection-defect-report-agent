"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict — never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or Pydantic models.

import json
from typing import Any, Dict, Optional

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Agent state for MFG-C2-062 CV Quality Inspection Classification & Defect Report.

    Backbone fields (user_input, status, session_id, node_history, error_log,
    hitl_*, validated_input, input_context, etc.) are inherited from AgentState.

    Domain fields below track the CV inspection pipeline:
      pre_process  → defect_desc_parse → jis_classify → severity_assess
                   → remediation_lookup → report_generate → output_gate → post_process
    """

    # ── pre_process output ─────────────────────────────────────────────────────
    # validated_input: str — inherited from AgentState (set by PreProcessNode)
    enriched_context: Optional[Dict[str, Any]]  # channel + source metadata
    caller_contract: Optional[str]  # JSON: the validated inspection record + its source

    # ── defect_desc_parse output ───────────────────────────────────────────────
    parsed_defect: Optional[Dict[str, Any]]  # structured defect info:
    #   {defect_type: str, location: str, coordinates: list[float],
    #    measurement: {value: float, unit: str} | None, inspection_timestamp: str}

    # ── jis_classify output ────────────────────────────────────────────────────
    jis_class_code: Optional[str]  # e.g. "JIS-B-0601-Rz", "JIS-G-0303-S01"
    jis_class_name: Optional[str]  # human-readable class label
    confidence_score: Optional[float]  # 0.0–1.0 classification confidence

    # ── severity_assess output ─────────────────────────────────────────────────
    severity_level: Optional[str]  # "Critical" | "Major" | "Minor" | "NoDefect"

    # ── remediation_lookup output ──────────────────────────────────────────────
    remediation_action: Optional[str]  # recommended corrective action text

    # ── report_generate output ─────────────────────────────────────────────────
    defect_report: Optional[str]  # ISO9001-format defect report text

    # ── output_gate output ─────────────────────────────────────────────────────
    human_review_required: Optional[bool]  # True → uncertain; must not auto-certify
    result: Optional[str]  # final output text (= defect_report)

    # ── post_process output ────────────────────────────────────────────────────
    formatted_output: Optional[str]  # shaped final response for caller


def to_json(value: Any) -> str:
    """Serialise a structured value for a state field.

    Structured values are stored as JSON strings because checkpoint
    serialisation does not carry bare containers safely across the nested
    graph boundary.
    """
    return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)


def from_json(value: Any, default: Any = None) -> Any:
    """Read a structured value back out of a state field; `default` on absence."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default
