"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict — never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or Pydantic models.
#
# TEL-C2-002 — Telecom Contract & Technical Document OCR & Extraction Agent
# Two-layer nested Cat 2 graph: outer backbone (AgentBaseGraph) + inner
# domain workflow (BaseGraph).  Fields below cover both layers.
#
# ADR-005 compliance: all dict/list-valued fields are stored as JSON-
# serialized Optional[str].  Use to_json() / from_json() helpers below
# at every producer and consumer node — one contract end-to-end.
# Never type a dict/list field as a bare dict/list; that causes msgpack
# serialization failures and a CoE Stage-6 state-contract finding.

import json
from typing import Any, NotRequired, Optional

from framework.schemas.agent_state import AgentState


def to_json(value: Any) -> Optional[str]:
    """Serialize a value to a JSON string for State storage (ADR-005)."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def from_json(value: Optional[str], default: Any = None) -> Any:
    """Deserialize a JSON string from State storage (ADR-005)."""
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


class State(AgentState):
    """Flat TypedDict for TEL-C2-002.

    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, etc.) are inherited from AgentState.

    ADR-005: dict/list fields use JSON-serialized Optional[str].
    formatted_output is NOT re-declared here — it is inherited from AgentState.
    """

    # ------------------------------------------------------------------
    # Outer layer — set by PreProcessNode (pre_process backbone, S-1)
    # ------------------------------------------------------------------

    # Validated and normalised JSON string of the document payload.
    # Produced by PreProcessNode; consumed by inner InputValidateNode.
    validated_input: NotRequired[Optional[str]]

    # JSON-serialised channel/request metadata dict (ADR-005: stored as str).
    # Shape: {"source": str, "channel": str, "document_id": str}
    enriched_context: NotRequired[Optional[str]]

    # ------------------------------------------------------------------
    # Inner layer — domain nodes (DomainWorkflowGraph)
    # ------------------------------------------------------------------

    # JSON-serialised parsed document payload (ADR-005: stored as str).
    # Shape: {document_id, document_type, content, pages (int),
    #   language, source, content_length (int), text_blocks (list),
    #   detected_type (str), ocr_quality (float)}
    document_data: NotRequired[Optional[str]]

    # JSON-serialised extracted-field dict (ADR-005: stored as str).
    # Shape: {document_type, fields: {category: {key: value}}, field_count (int)}
    extracted_fields: NotRequired[Optional[str]]

    # JSON-serialised confidence-scoring result (ADR-005: stored as str).
    # Shape: {per_field: {name: score}, overall_confidence (float),
    #   low_confidence_fields (list), threshold (float), needs_review (bool)}
    confidence_scores: NotRequired[Optional[str]]

    # Overall extraction confidence in [0.0, 1.0].
    # Set by ConfidenceScoreNode; surfaced to the caller in the result.
    overall_confidence: NotRequired[Optional[float]]

    # Final structured-JSON extraction result (pretty-printed JSON string).
    # Assembled by inner OutputFormatNode from extracted_fields +
    # confidence_scores + document metadata.
    extraction_result: NotRequired[Optional[str]]

    # ------------------------------------------------------------------
    # Outer layer — set by PostProcessNode (post_process backbone, S-3)
    # ------------------------------------------------------------------

    # Primary result surfaced to the caller.
    # Set to the same content as extraction_result after the S-3 gate passes.
    # formatted_output (from AgentState) is also set by PostProcessNode.
    result: NotRequired[Optional[str]]

    # ------------------------------------------------------------------
    # Caller-visible failure channel
    # ------------------------------------------------------------------

    # A constant drawn from contracts.FAILURE_REASONS — never node-authored
    # text, never an exception message, never a rejected value. error_log stays
    # the internal channel; this is the only failure detail a caller receives.
    failure_reason: NotRequired[Optional[str]]

    # JSON-serialised {credential_kind: count} for values removed from the
    # caller's document before processing (ADR-005: stored as str). Counts and
    # kinds only — the redacted text is never carried anywhere.
    redaction_counts: NotRequired[Optional[str]]

    # ------------------------------------------------------------------
    # Tracing / audit — framework-managed; do NOT write from node code
    # ------------------------------------------------------------------

    trace_id: NotRequired[Optional[str]]
    correlation_id: NotRequired[Optional[str]]
    # node_history inherited from AgentState
