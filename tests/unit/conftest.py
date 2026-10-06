"""Unit test fixtures for MFG-C2-062.

Patches emit_trace_event at each node module to avoid requiring a real
audit-logger backend during unit tests.  The framework's `shared.utils.audit_logger`
IS present in the real SDK wheel (agenticstar-agentcore==1.0.0); patching at the
node-module level (not via sys.modules) leaves the wheel's `shared` package intact
so `from shared.security import ...` inside framework/ nodes still resolves.
"""

import pytest
from unittest.mock import patch


_NODE_EMIT_TARGETS = [
    "src.nodes.pre_process_node.emit_trace_event",
    "src.nodes.post_process_node.emit_trace_event",
    "src.nodes.defect_description_parse_node.emit_trace_event",
    "src.nodes.jis_classify_node.emit_trace_event",
    "src.nodes.severity_assess_node.emit_trace_event",
    "src.nodes.remediation_lookup_node.emit_trace_event",
    "src.nodes.report_generate_node.emit_trace_event",
    "src.nodes.output_gate_node.emit_trace_event",
]


@pytest.fixture(autouse=True)
def patch_all_emit_trace_event():
    """Suppress all emit_trace_event calls in unit tests (no audit backend needed)."""
    from contextlib import ExitStack

    with ExitStack() as stack:
        for target in _NODE_EMIT_TARGETS:
            stack.enter_context(patch(target, lambda *a, **k: None))
        yield
