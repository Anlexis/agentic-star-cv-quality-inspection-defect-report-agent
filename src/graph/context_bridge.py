"""AgentCore Platform v1.0"""

# MFG-C2-062 — carries the validated caller contract across the outer/inner
# graph boundary.
#
# Why it exists: the framework invokes a nested graph as
# `subgraph.invoke(user_input, session_id=..., ctx=...)`. Only the request
# STRING crosses — neither the outer state nor the caller's structured
# invocation parameters (`input_context`) are forwarded. So the inspection
# record that the pre_process node validated would never reach the domain
# pipeline, and an inner node reading `state["input_context"]` would see an
# empty mapping on every real invocation while unit tests that seed the inner
# state directly kept passing.
#
# The two sanctioned subclass hooks bridge it without touching the framework:
#
#   CVInspectionWorkflowGraphNode.extract_input(state)   [runs BEFORE subgraph.invoke]
#       -> set_caller_contract(<validated contract>)
#   CVInspectionWorkflowGraph._extra_initial_state()     [runs INSIDE subgraph.invoke]
#       -> seeds the contract into the inner state
#
# What crosses is the VALIDATED contract only: every field has already passed
# its bounded, inert shape check and every number its finite-range check. The
# raw request body never travels.
#
# A ContextVar rather than a module global: it is per-thread and per-task, so
# two invocations running concurrently in one process cannot read each other's
# contract.

from contextvars import ContextVar
from typing import Any, Dict, Optional

_CALLER_CONTRACT: ContextVar[Optional[Dict[str, Any]]] = ContextVar("mfg_c2_062_caller_contract", default=None)


def set_caller_contract(contract: Optional[Dict[str, Any]]) -> None:
    """Stash the validated caller contract for the imminent inner-graph invoke."""
    _CALLER_CONTRACT.set(dict(contract) if contract else {})


def get_caller_contract() -> Dict[str, Any]:
    """Read (without consuming) the stashed contract; {} when none was set."""
    return _CALLER_CONTRACT.get() or {}
