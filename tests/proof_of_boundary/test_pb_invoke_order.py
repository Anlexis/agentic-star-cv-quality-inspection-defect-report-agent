# PB-6: Backbone Invoke-Order Verification — MFG-C2-062
#
# Verifies that the outer AgentBaseGraph backbone fires its 5 nodes in the
# correct order when called by a real VERIFIED_EXTERNAL caller:
#
#   InitializeNode → PreProcessNode → CVInspectionWorkflowGraphNode
#               → PostProcessNode → FinalizeNode
#
# Uses a real VERIFIED_EXTERNAL InvocationContext (NOT InvocationContext.for_internal())
# so the PreProcessNode trust gate (VERIFIED_EXTERNAL) is exercised on the same
# trust path a real caller would take.  An INTERNAL context masks the trust-trap
# and produces a false-green.
#
# _MAIN_SLOT_NODE : the class name of the GraphNode in the `main` backbone slot.
# _VALID_PAYLOAD  : a domain input that produces AgentStatus.SUCCESS end-to-end
#                   (non-SUCCESS short-circuits main→finalize, skipping post_process).
#                   deploy/invoke_payload.json carries this same value.

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import ManufacturingCVQualityInspectionAgent

# ── template-specific parameters ─────────────────────────────────────────────
_MAIN_SLOT_NODE = "CVInspectionWorkflowGraphNode"

# A well-formed JSON CV payload that produces SUCCESS through the full pipeline.
# Covers: parsed_defect → JIS classification → severity → remediation → report.
_VALID_PAYLOAD = (
    '{"defect_type": "crack", "location": "surface", '
    '"coordinates": [12.3, 45.6], "measurement": "0.8mm", '
    '"timestamp": "2026-06-29T10:00:00"}'
)

# Backbone execution order: InitializeNode → pre_process → main → post_process → FinalizeNode
_EXPECTED_ORDER = [
    "InitializeNode",
    "PreProcessNode",
    _MAIN_SLOT_NODE,
    "PostProcessNode",
    "FinalizeNode",
]


class TestBackboneInvokeOrder:
    """PB-6: backbone fires exactly 5 nodes in the correct order.

    Exercises the real external-caller trust path:
      caller=VERIFIED_EXTERNAL(1) ≥ PreProcessNode gate=VERIFIED_EXTERNAL(1) → PASS
      caller=VERIFIED_EXTERNAL(1) ≥ inner nodes=ANONYMOUS(0) → PASS
    """

    def test_backbone_order_success_path(self):
        """Full invoke on SUCCESS payload — assert 5-node backbone order."""
        agent = ManufacturingCVQualityInspectionAgent()
        agent.compile()

        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        result = agent.invoke(user_input=_VALID_PAYLOAD, ctx=ctx)

        assert (
            result["status"] == AgentStatus.SUCCESS
        ), f"Expected AgentStatus.SUCCESS but got {result['status']}. output={result.get('output', '')[:200]}"

        node_history = result.get("node_history", [])
        # node_history entries may be class instances, class types, or name strings;
        # normalise to class name strings for comparison.
        actual_names = [n if isinstance(n, str) else type(n).__name__ for n in node_history]

        assert (
            actual_names == _EXPECTED_ORDER
        ), f"Backbone invoke order mismatch.\nExpected: {_EXPECTED_ORDER}\nActual  : {actual_names}"

    def test_output_present_on_success(self):
        """result['output'] is populated on a SUCCESS invoke."""
        agent = ManufacturingCVQualityInspectionAgent()
        agent.compile()

        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        result = agent.invoke(user_input=_VALID_PAYLOAD, ctx=ctx)

        assert result["status"] == AgentStatus.SUCCESS
        # result["output"] is the canonical output key (not "formatted_output")
        assert result["output"], "result['output'] must be non-empty on a SUCCESS invoke"

    def test_anonymous_caller_rejected_by_pre_process(self):
        """ANONYMOUS caller is denied at PreProcessNode (VERIFIED_EXTERNAL gate).

        Verifies the trust boundary: an external caller without at least
        VERIFIED_EXTERNAL trust cannot reach domain processing.
        """
        agent = ManufacturingCVQualityInspectionAgent()
        agent.compile()

        ctx = InvocationContext(caller_trust_level=TrustLevel.ANONYMOUS)
        result = agent.invoke(user_input=_VALID_PAYLOAD, ctx=ctx)

        # A trust-gate denial returns AgentStatus.ERROR (not an exception)
        assert (
            result["status"] == AgentStatus.ERROR
        ), f"Expected AgentStatus.ERROR for ANONYMOUS caller but got {result['status']}"


class TestOutputGate:
    """Output gate — human review flag and credential withholding."""

    @staticmethod
    def _gate(state):
        from src.nodes.output_gate_node import OutputGateNode

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.nodes.output_gate_node.emit_trace_event", lambda *a, **k: None)
            return OutputGateNode().execute(state)

    @staticmethod
    def _state(report, confidence, severity, jis_code):
        return {
            "defect_report": report,
            "confidence_score": confidence,
            "severity_level": severity,
            "jis_class_code": jis_code,
            "node_history": [],
            "error_log": [],
            "caller_trust_level": "VERIFIED_EXTERNAL",
        }

    def test_output_gate_flags_low_confidence(self):
        """OutputGateNode sets human_review_required=True when confidence < 0.75."""
        result = self._gate(
            self._state("DEFECT REPORT — crack detected at (12.3, 45.6)", 0.60, "Minor", "JIS-B-0601-Rz")
        )

        assert result["status"] == AgentStatus.SUCCESS
        assert result["human_review_required"] is True, "Low-confidence classification must require human review"

    def test_output_gate_critical_severity_requires_review(self):
        """OutputGateNode sets human_review_required=True for Critical severity."""
        result = self._gate(self._state("CRITICAL defect detected", 0.92, "Critical", "JIS-G-0303-S01"))

        assert result["status"] == AgentStatus.SUCCESS
        assert (
            result["human_review_required"] is True
        ), "Critical severity must always require human review (never auto-certify)"

    def test_output_gate_withholds_credentials(self):
        """OutputGateNode withholds a report carrying a credential pattern.

        The report is not released in redacted form: the status is an error
        and every field that could carry the report onward is cleared.
        """
        result = self._gate(
            self._state("Report text api_key=SECRETABC123 — crack detected", 0.90, "Minor", "JIS-B-0601-Rz")
        )

        assert result["status"] == AgentStatus.ERROR
        assert result["result"] is None
        assert result["defect_report"] is None
        assert "SECRETABC123" not in str(result), "the matched credential must never appear in the gate's output"

    def test_output_gate_pass_high_confidence_nodeffect(self):
        """NoDefect with high confidence does NOT require human review."""
        result = self._gate(self._state("PASS — No defect detected", 0.95, "NoDefect", "JIS-PASS"))

        assert result["status"] == AgentStatus.SUCCESS
        assert result["human_review_required"] is False, "High-confidence NoDefect should not require human review"
