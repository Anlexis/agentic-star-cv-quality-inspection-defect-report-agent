"""AgentCore Platform v1.0"""

# DefectDescriptionParseNode — first step of the inner inspection pipeline.
#
# Reads the inspection record the request boundary already validated and
# publishes it as `parsed_defect` for the classification steps. It does NOT
# re-parse raw request text: the caller contract (src/services/caller_contract.py)
# is validated exactly once, at the outer pre_process boundary, and travels to
# this inner graph on the context bridge. A record that did not come through
# that boundary is refused rather than reconstructed from the request string —
# a silent fallback here would hide a broken bridge behind a plausible answer.
#
# Input:  caller_contract (JSON str) — seeded by the inner graph's initial state
# Output: parsed_defect (dict) with keys: defect_type, location, coordinates,
#         measurement ({value, unit} | None), inspection_timestamp.

from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json

_RECORD_KEYS = ("defect_type", "location", "coordinates", "measurement", "inspection_timestamp")


class DefectDescriptionParseNode(FunctionNode):
    """Publish the validated inspection record as the pipeline's structured defect."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        contract = from_json(state.get("caller_contract"), None)
        record = contract.get("record") if isinstance(contract, dict) else None

        emit_trace_event(
            "defect_parse_started",
            {"record_present": isinstance(record, dict)},
            state,
        )

        if not isinstance(record, dict) or any(key not in record for key in _RECORD_KEYS):
            emit_trace_event("defect_parse_failed", {"reason": "no_validated_record"}, state)
            return {
                "status": AgentStatus.ERROR,
                "error_log": ["DefectDescriptionParseNode: no validated inspection record reached the pipeline"],
            }

        parsed: Dict[str, Any] = {key: record[key] for key in _RECORD_KEYS}

        emit_trace_event(
            "defect_parse_complete",
            {
                "defect_type": parsed["defect_type"],
                "location": parsed["location"],
                "coordinate_count": len(parsed["coordinates"] or []),
                "record_source": contract.get("record_source", "unknown"),
            },
            state,
        )

        return {
            "parsed_defect": parsed,
            "status": AgentStatus.SUCCESS,
        }
