"""AgentCore Platform v1.0 — MFG-C2-062 inner domain workflow graph."""

# src/graph/domain_workflow_graph.py
#
# Inner BaseGraph for the CV Quality Inspection pipeline.
# Called by CVInspectionWorkflowGraphNode.get_subgraph() in graph.py.
#
# Pipeline (linear):
#   START → defect_desc_parse → jis_classify → severity_assess
#         → remediation_lookup → report_generate → output_gate → END
#
# All inner domain nodes carry required_trust_level = TrustLevel.ANONYMOUS
# (per Cat-2 hardened rules: GraphNode.execute() passes the outer
# InvocationContext unchanged; a real external caller arrives VERIFIED_EXTERNAL(1);
# ANONYMOUS(0) admits any caller — compliant with ADR-017 and review finding 5).
#
# The validated caller contract reaches this graph on the context bridge
# (src/graph/context_bridge.py): the framework hands a nested graph only the
# request string, so the record the outer boundary validated is seeded into
# the inner state by _extra_initial_state() instead.

from typing import Any, Dict

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_status import AgentStatus
from src.graph.context_bridge import get_caller_contract
from src.nodes.defect_description_parse_node import DefectDescriptionParseNode
from src.nodes.jis_classify_node import JISClassifyNode
from src.nodes.output_gate_node import OutputGateNode
from src.nodes.remediation_lookup_node import RemediationLookupNode
from src.nodes.report_generate_node import ReportGenerateNode
from src.nodes.severity_assess_node import SeverityAssessNode
from src.schemas.state import State, to_json


class CVInspectionWorkflowGraph(BaseGraph):
    """Inner domain workflow graph for CV quality inspection.

    Inherits BaseGraph (fully custom topology — no backbone slots).
    Implements all 7 BaseGraph abstract methods.

    Called by CVInspectionWorkflowGraphNode.get_subgraph() in graph.py.
    The sub_result returned by get_output() is passed to
    CVInspectionWorkflowGraphNode.merge_output() in graph.py.
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "cv_inspection_workflow"

    @property
    def state_schema(self) -> type:
        return State

    # ── Config validation ─────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """No mandatory config keys for the inner graph (built-in KB only)."""

    # ── Initial state ─────────────────────────────────────────────────────────

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the bridged caller contract into the inner state.

        The framework hands the inner graph only a string, so the validated
        inspection record travels on the context bridge and is republished here.
        Stored as a JSON string, because checkpoint serialization does not carry
        bare containers safely. An empty contract — the bridge was never set —
        makes the first inner step refuse, loudly, rather than re-deriving a
        record from the request text.
        """
        return {"caller_contract": to_json(get_caller_contract())}

    # ── Node registration ─────────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register all domain nodes — NO super() call (BaseGraph is abstract).

        All nodes instantiated with NO constructor args (SDK-v1 rule).
        """
        self._nodes["defect_desc_parse"] = DefectDescriptionParseNode()
        self._nodes["jis_classify"] = JISClassifyNode()
        self._nodes["severity_assess"] = SeverityAssessNode()
        self._nodes["remediation_lookup"] = RemediationLookupNode()
        self._nodes["report_generate"] = ReportGenerateNode()
        self._nodes["output_gate"] = OutputGateNode()

    # ── Edge wiring ───────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Wire linear domain pipeline.

        defect_desc_parse → jis_classify → severity_assess
            → remediation_lookup → report_generate → output_gate → END
        """
        self._sg.add_edge(START, "defect_desc_parse")
        self._sg.add_edge("defect_desc_parse", "jis_classify")
        self._sg.add_edge("jis_classify", "severity_assess")
        self._sg.add_edge("severity_assess", "remediation_lookup")
        self._sg.add_edge("remediation_lookup", "report_generate")
        self._sg.add_edge("report_generate", "output_gate")
        self._sg.add_edge("output_gate", END)

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(self, state: State) -> str:
        """Required by the BaseGraph contract. This graph is linear — never wired.

        Annotated with this graph's own State: LangGraph reads a path callable's
        annotation as its input schema and projects away every field the
        annotation does not declare, so an annotation naming the base state
        would make the routing fields permanently absent if this were wired.
        """
        return END if state.get("status") == AgentStatus.ERROR.value else "output_gate"

    # ── Output shape ──────────────────────────────────────────────────────────

    def get_output(self, state: State) -> Dict[str, Any]:
        """Shape the sub_result returned to CVInspectionWorkflowGraphNode.merge_output().

        Designed together with merge_output() in graph.py. The output falls
        back from `result` to `defect_report`, which is why the output gate
        clears BOTH when it withholds a report.
        """
        return {
            "output": state.get("result") or state.get("defect_report", ""),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
            "human_review_required": state.get("human_review_required", False),
            "severity_level": state.get("severity_level", ""),
            "jis_class_code": state.get("jis_class_code", ""),
        }
