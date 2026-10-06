"""AgentCore Platform v1.0"""

# OutputGateNode — last step of the inner inspection pipeline.
#
# Two responsibilities:
#   1. The output gate on the generated report. A report carrying a
#      credential-shaped value is WITHHELD — the step returns an error status
#      and clears every field that could carry the report onward. Redacting
#      the value and releasing the rest is not containment: the report would
#      still ship as a success, and the inner graph's output falls back from
#      `result` to `defect_report`, so clearing one field alone re-opens the
#      other. Both are cleared here.
#   2. The human-review disposition: an uncertain classification must never be
#      auto-certified. Low confidence, a Critical severity, or an unclassified
#      defect all require a qualified reviewer.
#
# Detector composition: the platform's own credential detector is the FLOOR —
# the platform's mandatory output gate calls the same function on this step's
# return value, and anything it catches that this gate missed would make the
# platform raise here and discard this step's update, clearing included. The
# local patterns are kept ON TOP of it because they catch shapes the platform's
# format-based patterns do not (an `api_key=...` assignment, a `password: ...`
# line, GitLab/GitHub/Slack token prefixes, a PEM private-key block). Wider is
# safe; narrower would be a bypass.

import re
from typing import Any, ClassVar, Dict, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from framework.security.credential_detector import detect_credentials
from shared.utils.audit_logger import emit_trace_event
from src.services.confidence import finite_confidence

# Shapes the platform detector does not carry. Each name is a closed-set label
# that may appear in an audit record or an error message; the matched text never
# does.
_LOCAL_CREDENTIAL_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    (
        "secret_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?key|token|secret|password|passwd|pwd|bearer|authorization)\s*[=:]\s*\S+"
        ),
    ),
    ("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{16,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("slack_token", re.compile(r"\bxox[abpr]-[A-Za-z0-9\-]{10,}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

_HUMAN_REVIEW_CONFIDENCE_THRESHOLD = 0.75
_HUMAN_REVIEW_SEVERITIES = {"Critical", "JIS-UNKNOWN"}

# Closed-set message: no caller or report text is ever interpolated into it.
WITHHELD_NOTICE = "Inspection report withheld — the generated report contained credential-shaped content."


def find_credential(text: str) -> Optional[str]:
    """Name the first credential shape in the text, or None.

    The platform detector runs first, so the set this gate refuses is a
    superset of the set the platform's gate would block; the local patterns
    only ever add to it.
    """
    if not isinstance(text, str) or not text:
        return None
    findings = detect_credentials(text)
    if findings:
        return str(findings[0].get("type", "credential"))
    for name, pattern in _LOCAL_CREDENTIAL_PATTERNS:
        if pattern.search(text):
            return name
    return None


class OutputGateNode(FunctionNode):
    """Output gate and final disposition step of the inner pipeline.

    Responsibilities:
    1. Credential scan on the generated report text; a hit withholds the report.
    2. Set human_review_required=True when:
       - classification confidence is below threshold, OR
       - severity is Critical or the defect is unclassified (per ISO9001 §8.7 —
         uncertain classifications must never be auto-certified as no-defect).
    3. Copy defect_report → result for the outer post_process step.

    Rule: NEVER auto-certify 'no defect' when confidence is uncertain.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        defect_report = state.get("defect_report") or ""
        confidence = finite_confidence(state.get("confidence_score"))
        severity = state.get("severity_level", "Unknown")
        jis_code = state.get("jis_class_code", "JIS-UNKNOWN")

        emit_trace_event(
            "output_gate_started",
            {
                "confidence_score": confidence,
                "severity_level": severity,
                "jis_class_code": jis_code,
                "report_length": len(defect_report),
            },
            state,
        )

        credential = find_credential(defect_report)
        if credential is not None:
            emit_trace_event(
                "output_gate_withheld",
                {"reason": "credential_shaped_content", "pattern": credential},
                state,
            )
            return {
                "status": AgentStatus.ERROR,
                # Every field that can carry the report onward is cleared: the
                # inner graph's output falls back from `result` to
                # `defect_report`, so leaving either one in place releases it.
                "result": None,
                "defect_report": None,
                "human_review_required": True,
                "error_log": [f"OutputGateNode: {WITHHELD_NOTICE} ({credential})"],
            }

        # Determine if human review is required
        low_confidence = confidence < _HUMAN_REVIEW_CONFIDENCE_THRESHOLD
        uncertain_severity = severity in _HUMAN_REVIEW_SEVERITIES or jis_code == "JIS-UNKNOWN"
        human_review = low_confidence or uncertain_severity

        emit_trace_event(
            "output_gate_complete",
            {
                "human_review_required": human_review,
                "low_confidence": low_confidence,
                "uncertain_severity": uncertain_severity,
            },
            state,
        )

        return {
            "human_review_required": human_review,
            "result": defect_report,
            "status": AgentStatus.SUCCESS,
        }
