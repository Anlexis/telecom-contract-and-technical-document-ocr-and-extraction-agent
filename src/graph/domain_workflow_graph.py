"""AgentCore Platform v1.0"""

# TEL-C2-002 — DomainWorkflowGraph (inner BaseGraph)
#
# This is the INNER graph for the Cat 2 two-layer nested architecture.
# It encapsulates the telecom document OCR & extraction pipeline:
#
#   START
#     → input_validate      (InputValidateNode)
#     → parse_document      (ParseDocumentNode)
#     → extract_fields      (ExtractFieldsNode)
#     → confidence_score    (ConfidenceScoreNode)
#     → output_format       (OutputFormatNode)
#     → END
#
# Called by DocumentExtractionGraphNode.get_subgraph() (graph.py).
# get_output() shapes the sub_result dict consumed by merge_output() there.
#
# Rules enforced:
#   ✅ Inherits BaseGraph (fully custom topology — no forced backbone)
#   ✅ Implements all 7 BaseGraph ABC methods
#   ✅ register_nodes() does NOT call super() (abstract in BaseGraph)
#   ✅ Does NOT register initialize / finalize (outer backbone concerns)
#   ✅ All inner nodes declare required_trust_level = TrustLevel.ANONYMOUS
#   ✅ get_output() designed together with DocumentExtractionGraphNode.merge_output()
#   ✅ All inner node ctors are empty-parens (no constructor args — CoE no-arg rule)
#   ❌ No Level-0 platform SDK imports
#   ❌ Not placed under src/subagents/

from typing import Any, Dict

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.nodes.confidence_score_node import ConfidenceScoreNode
from src.nodes.extract_fields_node import ExtractFieldsNode
from src.nodes.input_validate_node import InputValidateNode
from src.nodes.output_format_node import OutputFormatNode
from src.nodes.parse_document_node import ParseDocumentNode
from src.schemas.state import State


class DomainWorkflowGraph(BaseGraph):
    """Inner domain workflow graph for TEL-C2-002.

    Inherits BaseGraph directly for a fully custom node topology.
    Called by DocumentExtractionGraphNode.get_subgraph() in graph.py.

    Pipeline (linear):
        START
          → input_validate      (InputValidateNode)
          → parse_document      (ParseDocumentNode)
          → extract_fields      (ExtractFieldsNode)
          → confidence_score    (ConfidenceScoreNode)
          → output_format       (OutputFormatNode)
          → END

    All nodes are FunctionNode subclasses with ANONYMOUS trust_level.
    initialize / finalize are outer backbone concerns — not registered here.
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "tel_c2_002_document_extraction_workflow"

    @property
    def state_schema(self) -> type:
        return State

    # ── Config validation ─────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """No mandatory config for v1 rule-based inner graph."""
        pass

    # ── Node registration ─────────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register all 5 domain nodes.

        No super() call — BaseGraph.register_nodes() is abstract.
        Do NOT register initialize or finalize; those are outer backbone
        concerns handled by AgentBaseGraph in graph.py.
        Every key registered here is referenced in add_edges().
        All nodes are instantiated with empty-parens (no ctor args) —
        CoE no-arg ctor rule: SDK v1 FunctionNode subclasses take no arguments.
        """
        self._nodes["input_validate"] = InputValidateNode()
        self._nodes["parse_document"] = ParseDocumentNode()
        self._nodes["extract_fields"] = ExtractFieldsNode()
        self._nodes["confidence_score"] = ConfidenceScoreNode()
        self._nodes["output_format"] = OutputFormatNode()

    # ── Edge wiring ───────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Wire the linear document-extraction topology.

        Linear flow:
            input_validate → parse_document → extract_fields
            → confidence_score → output_format → END.

        No conditional branching — all paths through the extraction pipeline are
        linear in v1.  route() satisfies the ABC but is not used at runtime.
        """
        self._sg.add_edge(START, "input_validate")
        self._sg.add_edge("input_validate", "parse_document")
        self._sg.add_edge("parse_document", "extract_fields")
        self._sg.add_edge("extract_fields", "confidence_score")
        self._sg.add_edge("confidence_score", "output_format")
        self._sg.add_edge("output_format", END)

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(self, state: AgentState) -> str:
        """Conditional routing — required by BaseGraph ABC.

        Linear topology; add_conditional_edges() is not used, so this method
        is never called at runtime.  Returns END on error so an unexpected
        invocation does not re-enter a processing node.
        """
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return "output_format"

    # ── Output shape ──────────────────────────────────────────────────────────

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Shape the output dict returned to the outer graph as sub_result.

        This dict is received by DocumentExtractionGraphNode.merge_output()
        in graph.py as the `sub_result` argument.  Both methods are designed
        together to guarantee field-name consistency:

            Inner get_output() emits:   "extraction_result", "extracted_fields",
                                        "confidence_scores", "overall_confidence", "status"
            Outer merge_output() reads: sub_result.get(...) for each key above.
        """
        return {
            "extraction_result": state.get("extraction_result"),
            "extracted_fields": state.get("extracted_fields"),
            "confidence_scores": state.get("confidence_scores"),
            "overall_confidence": state.get("overall_confidence", 0.0),
            "status": state.get("status"),
            "node_history": state.get("node_history", []),
            "correlation_id": state.get("correlation_id"),
        }
