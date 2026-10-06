"""AgentCore Platform v1.0"""

# SeverityAssessNode
# Maps JIS classification → severity level (Critical / Major / Minor / NoDefect).
# Input: jis_class_code (str), confidence_score (float).
# Output: severity_level (str) — controlled vocabulary.

from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.confidence import finite_confidence

# ── Severity mapping ──────────────────────────────────────────────────────────
# Controlled vocabulary: Critical / Major / Minor / NoDefect
_SEVERITY_MAP: dict[str, str] = {
    "JIS-G-0303-S01": "Critical",  # Surface crack — structural risk
    "JIS-G-0303-S02": "Critical",  # Delamination — structural risk
    "JIS-G-0555-A1": "Critical",  # Internal void — structural integrity
    "JIS-G-0555-A2": "Major",  # Non-metallic inclusion — process control
    "JIS-G-0555-A3": "Major",  # Porosity — process control
    "JIS-B-0601-Rz": "Minor",  # Surface roughness scratch — cosmetic/wear
    "JIS-B-0659-1-D": "Minor",  # Dent — typically cosmetic
    "JIS-B-0721-B1": "Major",  # Burr — safety/assembly risk
    "JIS-G-0582-C1": "Minor",  # Corrosion — early stage; monitor
    "JIS-PASS": "NoDefect",  # No defect detected
    "JIS-UNKNOWN": "Critical",  # Unclassified → treat as Critical (fail-safe)
}

_CONFIDENCE_THRESHOLD = 0.75  # below this → escalate one severity level as fail-safe


class SeverityAssessNode(FunctionNode):
    """Assess defect severity from JIS classification + confidence score.

    Applies fail-safe escalation: when confidence is below the threshold,
    severity is escalated one level (Minor→Major, Major→Critical) because
    an uncertain classification should not be under-reported. A confidence
    that is not a finite number in [0, 1] is read as 0.0 — fully uncertain —
    so the comparison below can never take the permissive branch by accident.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    # Severity escalation order
    _ESCALATION: dict[str, str] = {
        "Minor": "Major",
        "Major": "Critical",
        "Critical": "Critical",  # already at maximum
        "NoDefect": "Minor",  # uncertain pass → treat as Minor
    }

    def execute(self, state: AgentState) -> Dict[str, Any]:
        jis_code = state.get("jis_class_code", "JIS-UNKNOWN")
        confidence = finite_confidence(state.get("confidence_score"))

        emit_trace_event(
            "severity_assess_started",
            {"jis_class_code": jis_code, "confidence_score": confidence},
            state,
        )

        base_severity = _SEVERITY_MAP.get(jis_code, "Critical")

        # Fail-safe: escalate if confidence is below threshold
        if confidence < _CONFIDENCE_THRESHOLD and base_severity != "Critical":
            severity = self._ESCALATION.get(base_severity, "Critical")
            escalated = True
        else:
            severity = base_severity
            escalated = False

        emit_trace_event(
            "severity_assess_complete",
            {
                "severity_level": severity,
                "escalated": escalated,
                "confidence_score": confidence,
            },
            state,
        )

        return {
            "severity_level": severity,
            "status": AgentStatus.SUCCESS,
        }
