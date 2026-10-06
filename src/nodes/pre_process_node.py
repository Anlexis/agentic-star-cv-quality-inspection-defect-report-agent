"""AgentCore Platform v1.0"""

# MFG-C2-062 — PreProcessNode (outer pre_process slot).
#
# The request boundary. Everything a caller can send is validated here, once,
# against the contract in src/services/caller_contract.py:
#   * the request string is length-capped and screened for disallowed
#     instructions, including chat-template control tokens;
#   * the structured invocation parameters are screened depth-first, keys
#     included, after parsing — so a payload that escaped its control tokens
#     as \u sequences is screened in its decoded form;
#   * the inspection record is bounded, its rendered fields restricted to an
#     inert alphabet, its numbers parsed by a finite + bounded parser;
#   * a refusal names the field and never repeats the value, and the notice it
#     publishes is drawn entirely from the contract's declared label and code
#     sets — see "The caller-visible ERROR envelope" below.
#
# The refusal is enforced HERE, in the node that owns the caller contract,
# rather than being left to the platform's own input screen. That screen
# scores some control-token forms and not others, and where it is absent or
# configured off the payload would reach the classification path and return
# success. This node refuses on its own, which is why the boundary tests call
# execute() directly with no wrapper in front of it.
#
# Node contract:
#  - extend FunctionNode; implement execute(state) -> dict
#  - return ONLY the fields this node changes (never the full state)
#  - return AgentStatus enum constants — never plain strings
#  - read input_context via state.get("input_context", {}) — read-only

import re
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import to_json
from src.services.caller_contract import (
    MAX_REQUEST_CHARS,
    REFUSAL_PREFIX,
    ContractError,
    refusal_notice,
    validate_request,
)

# The delivery channel label is caller data too; it is kept only when inert.
_CHANNEL_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

# ── The caller-visible ERROR envelope ─────────────────────────────────────────
# AgentBaseGraph.get_output() projects `formatted_output or result` with no
# status check, so what this node writes to formatted_output IS the error a
# caller receives. It carries closed-set labels only: a reason from
# ERROR_REASONS, then a field label and a refusal code that
# src/services/caller_contract.py declared and ContractError already forced into
# those sets. Nothing read from error_log, from a caught exception's str(), or
# from a caller value is ever projected.
#
# error_log keeps the same line — it is the internal channel the state reducer
# appends to, and the audit trail needs it. It is simply not the caller's.
_REASON_REQUEST_REFUSED = REFUSAL_PREFIX
#: Every value the caller-visible ERROR envelope may open with. This node has
#: exactly one non-success path, so the set has one member; a second reason
#: would mean a second path, not a second wording for this one.
ERROR_REASONS: frozenset[str] = frozenset({_REASON_REQUEST_REFUSED})


def _contain(reason: str, new_errors: Optional[List[str]] = None) -> Dict[str, Any]:
    """Return the ERROR result for ANY non-success path — the single error shape.

    *reason* is the caller-visible notice: it opens with a member of
    ERROR_REASONS and continues only with labels the contract declared.
    `result` is cleared because the framework falls back to it when
    formatted_output is falsy, and the notice is truthy so that fallback never
    opens. *new_errors*, when given, go to `error_log` — the internal channel —
    and nothing is ever read back out of it.
    """
    contained: Dict[str, Any] = {
        "status": AgentStatus.ERROR,
        "formatted_output": reason,
        "result": None,
    }
    if new_errors:
        contained["error_log"] = list(new_errors)
    return contained


class PreProcessNode(FunctionNode):
    """Validate the caller request and produce the contract the pipeline runs on.

    Input state keys:
        user_input:    the request string (JSON record or plain-text description)
        input_context: structured invocation parameters (read-only)

    Output state keys (partial dict):
        validated_input:  the screened request string
        caller_contract:  JSON string — the validated inspection record + source
        enriched_context: delivery metadata
        status:           AgentStatus.SUCCESS, or ERROR on a refusal
        error_log:        (on refusal) one message naming the offending field
    """

    # This is the external-facing security boundary: every caller must hold at
    # least VERIFIED_EXTERNAL trust to reach the domain pipeline.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        user_input = state.get("user_input", "")
        input_context = state.get("input_context", {})  # read-only

        try:
            contract = validate_request(user_input, input_context)
        except ContractError as refusal:
            # The field is named so the caller can act; the value never appears.
            #
            # The refusal is also written to formatted_output, because the
            # envelope surfaces that field and nothing else: the error log is
            # not part of it, so a refusal that lived only there would reach the
            # caller as an error with no reason at all.
            #
            # The notice is REBUILT from the exception's two closed-set
            # attributes rather than interpolated from the exception. An
            # interpolated exception is how text from outside the declared
            # vocabulary reaches a caller in the first place — it publishes
            # whatever the raise site happened to hold, and the type cannot say
            # no. refusal_notice() can only emit a declared label and a declared
            # code, so the envelope is closed by construction and not by the
            # habits of the current raise sites.
            emit_trace_event(
                "pre_process_rejected",
                {"field": refusal.field, "code": refusal.code},
                state,
            )
            notice = refusal_notice(refusal.field, refusal.code)
            return _contain(notice, new_errors=[notice])

        record = contract["record"]
        channel_raw = input_context.get("channel") if isinstance(input_context, dict) else None
        channel = channel_raw if isinstance(channel_raw, str) and _CHANNEL_RE.match(channel_raw) else "unknown"

        emit_trace_event(
            "pre_process_accepted",
            {
                "record_source": contract["record_source"],
                "defect_type": record["defect_type"],
                "channel": channel,
            },
            state,
        )

        return {
            "validated_input": str(user_input).strip()[:MAX_REQUEST_CHARS],
            # Structured values are stored as JSON strings: checkpoint
            # serialization does not carry bare containers safely.
            "caller_contract": to_json(contract),
            "enriched_context": {
                "source": "ManufacturingCVQualityInspectionAgent",
                "channel": channel,
                "record_source": contract["record_source"],
            },
            "status": AgentStatus.SUCCESS,
        }
