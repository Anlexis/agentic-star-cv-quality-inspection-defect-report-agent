"""AgentCore Platform v1.0 — MFG-C2-062 outer Cat-2 graph."""

# Cat 2 — AgentBaseGraph (outer) + GraphNode in `main` slot → inner BaseGraph.
#
# Outer backbone (fixed, identical to Cat 1):
#   START → initialize → pre_process → main[CVInspectionWorkflowGraphNode]
#         → post_process → finalize → END
#
# Inner domain workflow (CVInspectionWorkflowGraph):
#   defect_desc_parse → jis_classify → severity_assess
#   → remediation_lookup → report_generate → output_gate
#
# Directory layout:
#   src/graph/graph.py                 <- outer graph (this file)
#   src/graph/domain_workflow_graph.py <- inner graph (inspection topology)
#   src/graph/context_bridge.py        <- validated caller contract across the boundary
#
# Class name MUST match config/agent.yaml `class:` AND src/api/server.py import.
# agent.yaml: class: "src.graph.graph.ManufacturingCVQualityInspectionAgent"

from pathlib import Path
from typing import Any, ClassVar, Dict

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.trust_level import TrustLevel
from framework.utils.config_loader import load_agent_config
from src.graph.context_bridge import set_caller_contract
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State, from_json

# Repo root: src/graph/graph.py -> parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]


def runtime_config() -> Dict[str, Any]:
    """Load config/config.yaml — the live runtime parameters.

    The registry loads this file and passes it to the graph constructor; the
    standalone HTTP entry point does the same, so `max_retry` is live in both
    deployments rather than declared and ignored.

    Reading the static manifest (config/agent.yaml) here instead would return
    nothing: the manifest carries identity and compile-time requirements only,
    and a reader pointed at it degrades silently to defaults.
    """
    loaded = load_agent_config(_REPO_ROOT)
    return dict(loaded) if isinstance(loaded, dict) else {}


class CVInspectionWorkflowGraphNode(GraphNode):
    """GraphNode wrapping the inner CVInspectionWorkflowGraph.

    Placed in the `main` slot of ManufacturingCVQualityInspectionAgent.
    The inner graph holds all domain nodes (defect parsing, JIS classification,
    severity assessment, remediation lookup, report generation, output gate).
    """

    # "propagate": re-raise inner graph exceptions as SubgraphError (fail-fast).
    # An inner error status is re-raised the same way, so the outer state never
    # receives a merged result on a non-success path.
    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    # Inner nodes are ANONYMOUS; GraphNode itself has no external-facing trust gate.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def get_subgraph(self) -> Any:
        """Instantiate and return the inner CVInspectionWorkflowGraph."""
        from src.graph.domain_workflow_graph import CVInspectionWorkflowGraph

        return CVInspectionWorkflowGraph()

    def extract_input(self, state: AgentState) -> str:
        """Return the request string, and bridge the validated caller contract.

        The framework hands only a string to the inner graph, so the validated
        inspection record travels on the bridge instead — set here, one step
        before the inner invoke, and read by the inner graph's initial-state
        hook. Only the contract the pre_process node already validated crosses.
        """
        set_caller_contract(from_json(state.get("caller_contract"), {}) or {})
        return str(state.get("validated_input") or state.get("user_input", ""))

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map inner sub_result fields back into the outer state.

        Returns ONLY changed keys (never full state).
        Designed together with CVInspectionWorkflowGraph.get_output().
        """
        return {
            "result": sub_result.get("output", ""),
            "status": sub_result.get("status"),
            "human_review_required": sub_result.get("human_review_required", False),
            "severity_level": sub_result.get("severity_level", ""),
            "jis_class_code": sub_result.get("jis_class_code", ""),
        }


class ManufacturingCVQualityInspectionAgent(AgentBaseGraph):
    """CV Quality Inspection Classification & Defect Report Agent — MFG-C2-062.

    Cat 2 nested outer graph. Domain logic is encapsulated in
    CVInspectionWorkflowGraphNode (the `main` slot). The fixed backbone
    (initialize → pre_process → main → post_process → finalize) is provided
    by AgentBaseGraph. Do NOT override add_edges().

    Class name matches:
      config/agent.yaml   class: "src.graph.graph.ManufacturingCVQualityInspectionAgent"
      src/api/server.py   from src.graph.graph import ManufacturingCVQualityInspectionAgent
    """

    @property
    def name(self) -> str:
        return "ManufacturingCVQualityInspectionAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects InitializeNode + FinalizeNode
        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = CVInspectionWorkflowGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.
