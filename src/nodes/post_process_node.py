"""AgentCore Platform v1.0"""

from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event


class PostProcessNode(FunctionNode):
    """Format and finalise the defect inspection output.

    Reads result (set by OutputGateNode via inner graph merge) and shapes it
    into formatted_output.  Appends a human-review notice when required.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        result = state.get("result", "")
        human_review = state.get("human_review_required", False)

        emit_trace_event(
            "post_process_started",
            {"human_review_required": human_review, "result_length": len(result or "")},
            state,
        )

        if not result:
            emit_trace_event("post_process_no_result", {}, state)
            return {
                "formatted_output": "No inspection result available.",
                "status": AgentStatus.SUCCESS,
            }

        formatted = result
        if human_review:
            formatted = (
                formatted
                + "\n\n"
                + "⚠ HUMAN REVIEW REQUIRED — This result has not been auto-certified. "
                + "A qualified Quality Engineer must review before disposition."
            )

        emit_trace_event(
            "post_process_complete",
            {"formatted_output_length": len(formatted)},
            state,
        )

        return {
            "formatted_output": formatted,
            "status": AgentStatus.SUCCESS,
        }
