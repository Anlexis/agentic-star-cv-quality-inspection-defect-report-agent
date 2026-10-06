"""AgentCore Platform v1.0"""

# ReportGenerateNode
# Generates an ISO9001-format defect report from the accumulated inspection state.
# Input: parsed_defect, jis_class_code, jis_class_name, confidence_score,
#        severity_level, remediation_action.
# Output: defect_report (str) — ISO9001-format text.
#
# The report is line-delimited and every line is a labelled field, so a caller
# value carrying a newline could manufacture a line of its own — including the
# "Decision" line, which is the field a reader acts on. Nothing here renders a
# caller string as received: the record fields arrive validated by the caller
# contract (inert codes, bounded finite numbers, a parsed measurement, an
# ISO-8601 timestamp), and the numeric values are re-rendered from their parsed
# form rather than echoed.

from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.caller_contract import MAX_COORDINATES, inert_code, is_timestamp
from src.services.confidence import finite_confidence

_ACCEPT_CONFIDENCE = 0.75


def _rendered_code(value: Any, fallback: str = "unknown") -> str:
    """Render a record code only if it is inert; otherwise render the fallback.

    The record has already been validated, so this is a second reading of the
    same rule at the point of rendering: a field that somehow arrived outside
    the inert alphabet is not printed, whatever it contains.
    """
    try:
        return inert_code(value, "record")
    except ValueError:
        return fallback


def _rendered_coordinates(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "N/A"
    numbers: List[str] = []
    for item in value[:MAX_COORDINATES]:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return "N/A"
        numbers.append(str(round(float(item), 3)))
    return f"({', '.join(numbers)})"


def _rendered_measurement(value: Any) -> str:
    if not isinstance(value, dict):
        return "N/A"
    magnitude = value.get("value")
    unit = value.get("unit")
    if isinstance(magnitude, bool) or not isinstance(magnitude, (int, float)) or unit not in ("mm", "um", "cm"):
        return "N/A"
    return f"{float(magnitude):g} {unit}"


class ReportGenerateNode(FunctionNode):
    """Generate an ISO9001-format defect inspection report.

    Assembles all inspection findings into a structured report string.
    The report follows the ISO9001 non-conformance documentation format:
    header, defect identification, classification, severity, remediation,
    and a disposition recommendation.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        parsed_defect = state.get("parsed_defect") or {}
        jis_code = state.get("jis_class_code", "N/A")
        jis_name = state.get("jis_class_name", "N/A")
        confidence = finite_confidence(state.get("confidence_score"))
        severity = state.get("severity_level", "Unknown")
        remediation = state.get("remediation_action", "Not determined")

        emit_trace_event(
            "report_generate_started",
            {"jis_class_code": jis_code, "severity_level": severity},
            state,
        )

        # ── Build ISO9001 NCR structure ────────────────────────────────────────
        defect_type = _rendered_code(parsed_defect.get("defect_type"))
        location = _rendered_code(parsed_defect.get("location"))
        coord_str = _rendered_coordinates(parsed_defect.get("coordinates"))
        measurement = _rendered_measurement(parsed_defect.get("measurement"))
        timestamp = parsed_defect.get("inspection_timestamp") or ""
        # The timestamp was validated against an ISO-8601 shape; anything else
        # that reaches here is not printed.
        timestamp = timestamp.strip() if is_timestamp(timestamp) else ""

        disposition = self._disposition(severity, confidence)

        report_lines = [
            "═══════════════════════════════════════════════════════════",
            "  DEFECT INSPECTION REPORT — ISO9001 Non-Conformance Record",
            "═══════════════════════════════════════════════════════════",
            "  Template     : MFG-C2-062 (CV Quality Inspection Agent)",
            f"  Timestamp    : {timestamp or 'N/A'}",
            "───────────────────────────────────────────────────────────",
            "  DEFECT IDENTIFICATION",
            f"  Type         : {defect_type}",
            f"  Location     : {location}",
            f"  Coordinates  : {coord_str}",
            f"  Measurement  : {measurement}",
            "───────────────────────────────────────────────────────────",
            "  CLASSIFICATION",
            f"  JIS Code     : {jis_code}",
            f"  JIS Class    : {jis_name}",
            f"  Confidence   : {round(confidence * 100, 1)}%",
            "───────────────────────────────────────────────────────────",
            "  SEVERITY ASSESSMENT",
            f"  Level        : {severity}",
            "───────────────────────────────────────────────────────────",
            "  REMEDIATION",
            f"  Action       : {remediation}",
            "───────────────────────────────────────────────────────────",
            "  DISPOSITION",
            f"  Decision     : {disposition}",
            "═══════════════════════════════════════════════════════════",
        ]

        report = "\n".join(report_lines)

        emit_trace_event(
            "report_generate_complete",
            {
                "report_length": len(report),
                "severity_level": severity,
                "disposition": disposition,
            },
            state,
        )

        return {
            "defect_report": report,
            "status": AgentStatus.SUCCESS,
        }

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _disposition(severity: str, confidence: float) -> str:
        """Return the ISO9001 NCR disposition based on severity and confidence."""
        if severity == "NoDefect" and confidence >= _ACCEPT_CONFIDENCE:
            return "ACCEPT — No defect detected at required confidence level"
        if severity == "Critical":
            return "REJECT — Quarantine and escalate; do not ship"
        if severity == "Major":
            return "HOLD — Quarantine for further evaluation before release"
        if severity == "Minor":
            return "REWORK — Address defect; re-inspect before release"
        return "HOLD — Severity indeterminate; manual review required"
