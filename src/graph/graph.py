"""AgentCore Platform v1.0"""

# TEL-C2-002 — Outer graph (AgentBaseGraph; Cat 2 two-layer nested architecture)
#
# Architecture (Cat 2):
#
#   Outer backbone (fixed — identical to Cat 1, do NOT override add_edges()):
#     START → initialize → pre_process → main → {route} → post_process → finalize → END
#                                             ↓ (RETRY, max 3)
#                                          pre_process
#
#   `main` slot is a GraphNode subclass (DocumentExtractionGraphNode) that
#   delegates the full domain workflow to DomainWorkflowGraph (inner BaseGraph).
#
#   Domain complexity is fully encapsulated inside the inner graph. The outer
#   backbone is never modified.
#
# Directory layout:
#   src/graph/graph.py                 ← outer graph (this file)
#   src/graph/domain_workflow_graph.py ← inner graph (multi-step topology)
#
# Rules enforced:
#   ✅ TelecomContractDocumentOCRAgent inherits AgentBaseGraph (L1 Base)
#   ✅ super().register_nodes() called first (fills initialize + finalize)
#   ✅ DocumentExtractionGraphNode assigned to self._nodes["main"]
#   ✅ PreProcessNode (VERIFIED_EXTERNAL) in pre_process slot (S-1 gate)
#   ✅ PostProcessNode (ANONYMOUS) in post_process slot (S-3 output gate)
#   ✅ merge_output() returns only changed keys
#   ✅ _parent_config() forwards the runtime config to the inner graph
#   ✅ get_output() surfaces the domain result on a successful invoke (fail-closed)
#   ✅ class name matches config/agent.yaml class: field exactly
#   ❌ add_edges() NOT overridden on the outer graph
#   ❌ No Level-0 platform SDK imports

import os
from typing import Any, ClassVar, Dict, Optional

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.graph.base_graph import BaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas import contracts
from src.schemas.state import State

# Runtime parameters — config/config.yaml at the repo root (three levels up from
# this file: src/graph/graph.py → src/graph → src → <repo root>).
#
# This used to read the `agent.config` block of config/agent.yaml. The manifest
# is flat now and has no such block, so that reader would return {} on every
# call and every declared runtime value — max_retry, timeout_s and the whole
# llm block — would silently go back to a framework default while still being
# published as configuration.
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
    "config.yaml",
)


def _load_runtime_config() -> Dict[str, Any]:
    """Return the parsed contents of config/config.yaml.

    Best-effort: a missing or unparseable file yields ``{}`` so graph
    construction never breaks. PyYAML is loaded lazily — it is a framework
    runtime dependency, so importing it on demand avoids a hard module-load
    coupling.
    """
    try:
        import yaml

        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


class DocumentExtractionGraphNode(GraphNode):
    """GraphNode subclass assigned to the `main` slot of TelecomContractDocumentOCRAgent.

    Wraps DomainWorkflowGraph (inner Cat 2 BaseGraph).
    Called by AgentBaseGraph backbone after pre_process and before post_process.

    Contracts:
      get_subgraph()  — instantiate and return DomainWorkflowGraph
      extract_input() — pull validated_input (S-1 output) from outer state
      merge_output()  — map sub_result fields into outer state delta (changed keys only)
      error_strategy  — "propagate": re-raise inner errors as SubgraphError (fail-fast)
    """

    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    def get_subgraph(self) -> "BaseGraph":
        """Instantiate and return the inner domain workflow graph.

        DomainWorkflowGraph is imported lazily (inside the method) to avoid
        circular-import risk at module load time and to match the Cat 2 pattern.
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=self._parent_config())

    def extract_input(self, state: AgentState) -> str:
        """Return the string input passed into inner_graph.invoke().

        PreProcessNode (S-1) validates and normalises the raw user_input and
        writes the result to validated_input.  Prefer that; fall back to
        user_input if validated_input is absent (e.g. in unit tests).
        """
        return str(state.get("validated_input") or state.get("user_input") or "")

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map inner graph sub_result back into the outer state delta.

        sub_result is the dict returned by DomainWorkflowGraph.get_output().
        Returns ONLY changed keys — never the full state.

        Key coupling (designed together with DomainWorkflowGraph.get_output()):
          Inner get_output() emits  → "extraction_result", "extracted_fields",
                                       "confidence_scores", "overall_confidence", "status"
          This merge_output() reads → sub_result.get(...) for each of these keys.

        PostProcessNode (outer post_process) reads extraction_result +
        overall_confidence from state to apply the S-3 gate and set formatted_output.
        """
        return {
            "extraction_result": sub_result.get("extraction_result"),
            "extracted_fields": sub_result.get("extracted_fields"),
            "confidence_scores": sub_result.get("confidence_scores"),
            "overall_confidence": sub_result.get("overall_confidence", 0.0),
            "status": sub_result.get("status"),
        }

    def _parent_config(self) -> Dict[str, Any]:
        """Forward the declared runtime config to the inner graph.

        Reads config/config.yaml and exposes the declared settings under the
        LangGraph ``configurable`` key. Only keys that are actually declared
        (non-None) are forwarded; absent keys are omitted so the inner nodes
        fall back to their deterministic defaults.

        The ``llm.*`` block it carries is declared-but-inactive: this template
        invokes no language model, and no node reads those keys. It is forwarded
        rather than dropped so a later model-backed revision inherits a wired
        channel, and it is named as inactive in the operation guide so nobody
        tunes `temperature` expecting an effect.
        """
        cfg = _load_runtime_config()
        llm = cfg.get("llm", {}) or {}
        declared = {
            "system_prompt_template": llm.get("system_prompt_template"),
            "temperature": llm.get("temperature"),
            "max_tokens": llm.get("max_tokens"),
        }
        return {"configurable": {k: v for k, v in declared.items() if v is not None}}


class TelecomContractDocumentOCRAgent(AgentBaseGraph):
    """Outer graph for TEL-C2-002 (Cat 2 — DocGenerationAgent).

    Inherits AgentBaseGraph directly (L1 Base). Domain logic is fully
    encapsulated in DocumentExtractionGraphNode (main slot), which delegates to
    DomainWorkflowGraph (inner BaseGraph).

    Backbone (fixed — identical to Cat 1):
        START → initialize → pre_process → main → post_process → finalize → END

    register_nodes() and get_output() are the only overrides:
      - super().register_nodes() fills: initialize, finalize (framework defaults)
      - pre_process: PreProcessNode    (VERIFIED_EXTERNAL — S-1 trust gate)
      - main:        DocumentExtractionGraphNode (delegates to DomainWorkflowGraph)
      - post_process: PostProcessNode  (ANONYMOUS — S-3 output gate)
      - get_output(): surfaces the domain result (SR #12; S-3 fail-closed)

    add_edges() is NOT overridden — backbone wiring belongs to the framework.

    Class name MUST match config/agent.yaml `class:` field exactly.
    server.py imports this as `Graph` via the alias below.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Default the runtime config to config/config.yaml when none is passed.

        The platform constructs the agent with the declared runtime config, but
        the standalone entry point constructs it bare. Without this default the
        two paths behave differently: `max_retry` and `timeout_s` are published
        as configuration, and on the standalone path the framework would fall
        back to its own defaults with nothing saying so. Passing a config
        explicitly still wins, so a caller can override.
        """
        super().__init__(config if config is not None else _load_runtime_config())

    @property
    def name(self) -> str:
        """Agent identifier registered with AgentRegistry."""
        return "TelecomContractDocumentOCRAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        """Fill all 5 backbone slots.

        super().register_nodes() MUST be called first — it injects the
        framework's default InitializeNode (sets schema_version, session_id,
        trust_level) and FinalizeNode (builds response_metadata, total_time_ms).
        """
        super().register_nodes()  # fills: initialize, finalize

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = DocumentExtractionGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Surface the domain extraction result on the outer invoke() return.

        AgentBaseGraph.get_output() returns only the minimal
        ``{output, status, trace_id, correlation_id, node_history}`` envelope, so
        on the compiled outer graph the structured domain result was dropped and
        every field came back None to the caller even on a successful
        extraction. This override extends the base envelope.

        Output-gate invariant preserved (fail-closed):
          * ``formatted_output`` / ``result`` are the post-gate, caller-facing
            values written by PostProcessNode. ``extraction_result`` is exposed
            as that same gated value — NEVER the pre-gate raw
            ``state["extraction_result"]`` — so the gate cannot be bypassed.
          * The structured fields (extracted_fields / confidence_scores /
            overall_confidence) are surfaced ONLY on the gated success path. On
            any non-success outcome they are withheld.

        Failure channel:
          on a non-success outcome the caller receives a constant drawn from
          ``contracts.FAILURE_REASONS`` and nothing else. ``error_log`` carries
          node-authored text and, where a node interpolated a caught exception,
          upstream text — so it stays internal and is never projected here.
          Truncating or redacting it would not make it a closed set; only
          choosing from a closed set does.
        """
        output: Dict[str, Any] = super().get_output(state)
        succeeded = state.get("status") == AgentStatus.SUCCESS.value
        gated_result = state.get("formatted_output") or state.get("result")

        # Caller-facing, already gated values (safe on both paths).
        output["formatted_output"] = state.get("formatted_output") if succeeded else None
        output["result"] = state.get("result") if succeeded else None
        output["extraction_result"] = gated_result if succeeded else None

        # Structured domain result — surfaced only on the gated success path.
        output["extracted_fields"] = state.get("extracted_fields") if succeeded else None
        output["confidence_scores"] = state.get("confidence_scores") if succeeded else None
        output["overall_confidence"] = state.get("overall_confidence") if succeeded else None

        if succeeded:
            output["failure_reason"] = None
        else:
            reason = state.get("failure_reason")
            if reason not in contracts.FAILURE_REASONS:
                # Anything the template did not itself choose — a framework
                # rejection, an unhandled exception — collapses to the generic
                # constant rather than passing an unvetted value through.
                reason = contracts.REASON_REQUEST_FAILED
            output["failure_reason"] = reason
            output["output"] = {"reason": reason}
        return output

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.


# Alias for backward compat (server.py imports Graph).
# Class name TelecomContractDocumentOCRAgent matches config/agent.yaml class: field.
Graph = TelecomContractDocumentOCRAgent
