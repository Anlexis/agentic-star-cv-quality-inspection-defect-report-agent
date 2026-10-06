"""AgentCore Platform v1.0"""

# JISClassifyNode
# Classifies the parsed defect according to JIS (Japanese Industrial Standards).
# Input: parsed_defect (dict) from DefectDescriptionParseNode.
# Output: jis_class_code (str), jis_class_name (str), confidence_score (float).

from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

# ── JIS classification knowledge base (built-in; no external call) ────────────
# Maps defect_type → (jis_code, jis_name, base_confidence)
_JIS_DEFECT_MAP: dict[str, tuple[str, str, float]] = {
    "crack": ("JIS-G-0303-S01", "JIS Surface Crack (Class S01)", 0.92),
    "scratch": ("JIS-B-0601-Rz", "JIS Surface Roughness Rz (scratch)", 0.88),
    "dent": ("JIS-B-0659-1-D", "JIS Dent / Indentation (Class D)", 0.85),
    "void": ("JIS-G-0555-A1", "JIS Internal Void / Inclusion (A1)", 0.80),
    "burr": ("JIS-B-0721-B1", "JIS Burr / Sharp Edge (Class B1)", 0.87),
    "delamination": ("JIS-G-0303-S02", "JIS Delamination (Class S02)", 0.83),
    "inclusion": ("JIS-G-0555-A2", "JIS Non-metallic Inclusion (A2)", 0.79),
    "porosity": ("JIS-G-0555-A3", "JIS Porosity (A3)", 0.78),
    "corrosion": ("JIS-G-0582-C1", "JIS Corrosion / Rust (Class C1)", 0.84),
    "no-defect": ("JIS-PASS", "No Defect Detected (Pass)", 0.95),
    "no defect": ("JIS-PASS", "No Defect Detected (Pass)", 0.95),
}

_UNKNOWN_CODE = "JIS-UNKNOWN"
_UNKNOWN_NAME = "Unclassified Defect (Manual Review Required)"
_UNKNOWN_CONF = 0.40  # below threshold → triggers human review


class JISClassifyNode(FunctionNode):
    """Classify the parsed defect according to Japanese Industrial Standards (JIS).

    Uses a built-in mapping for the most common MFG defect types.
    Unrecognised defect types produce a low-confidence UNKNOWN classification,
    which triggers the human-review flag in OutputGateNode.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        parsed_defect = state.get("parsed_defect") or {}
        defect_type = str(parsed_defect.get("defect_type", "")).lower().strip()

        emit_trace_event(
            "jis_classify_started",
            {"defect_type": defect_type},
            state,
        )

        if not defect_type or defect_type == "unknown":
            emit_trace_event(
                "jis_classify_unknown",
                {"defect_type": defect_type, "confidence": _UNKNOWN_CONF},
                state,
            )
            return {
                "jis_class_code": _UNKNOWN_CODE,
                "jis_class_name": _UNKNOWN_NAME,
                "confidence_score": _UNKNOWN_CONF,
                "status": AgentStatus.SUCCESS,
            }

        # Look up defect type in built-in JIS map
        code, name, base_conf = _JIS_DEFECT_MAP.get(defect_type, (_UNKNOWN_CODE, _UNKNOWN_NAME, _UNKNOWN_CONF))

        # Reduce confidence when coordinates are missing (less evidence)
        coords = parsed_defect.get("coordinates", [])
        confidence = base_conf if coords else max(base_conf - 0.10, 0.40)

        emit_trace_event(
            "jis_classify_complete",
            {
                "jis_class_code": code,
                "confidence_score": round(confidence, 3),
            },
            state,
        )

        return {
            "jis_class_code": code,
            "jis_class_name": name,
            "confidence_score": round(confidence, 3),
            "status": AgentStatus.SUCCESS,
        }
