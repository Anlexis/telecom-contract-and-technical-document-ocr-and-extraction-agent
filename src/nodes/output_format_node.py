"""AgentCore Platform v1.0"""

# TEL-C2-002 — OutputFormatNode (inner domain node 5, last in DomainWorkflowGraph)
# Assembles the final structured-JSON extraction result from extracted_fields,
# confidence_scores, and document metadata.  This is the last inner node — it
# produces the extraction_result string that the outer PostProcessNode S-3-gates.
#
# Inner node — ANONYMOUS trust (review finding 5, corrected 2026-07-02).
# Returns ONLY the state keys this node writes (partial-dict contract).

import json
import logging
from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas import contracts
from src.schemas.state import from_json

logger = logging.getLogger(__name__)


def _assemble_result(
    document_data: Dict[str, Any],
    extracted: Dict[str, Any],
    confidence: Dict[str, Any],
    redaction_counts: Dict[str, int],
) -> str:
    """Assemble the final structured-JSON extraction document (pretty-printed str).

    The `redaction` block is part of the published schema, not a debug aid. A
    consumer reading a field value that contains a redaction sentinel has to be
    able to tell "this text was removed on purpose" from "this is what the
    document said", and `redacted_fields` names exactly which fields that is.
    Only KINDS and COUNTS of removed credentials are reported — never the values.
    """
    result_doc: Dict[str, Any] = {
        "document": {
            "document_id": document_data.get("document_id", "unknown"),
            "document_type": document_data.get("document_type", "unknown"),
            "detected_type": document_data.get("detected_type", "unknown"),
            "pages": document_data.get("pages", 0),
            "language": document_data.get("language", "und"),
            "source": document_data.get("source", "unknown"),
            "ocr_quality": document_data.get("ocr_quality", 0.0),
        },
        "extracted_fields": extracted.get("fields", {}),
        "field_count": extracted.get("field_count", 0),
        "confidence": {
            "overall_confidence": confidence.get("overall_confidence", 0.0),
            "per_field": confidence.get("per_field", {}),
            "low_confidence_fields": confidence.get("low_confidence_fields", []),
            "threshold": confidence.get("threshold"),
            "needs_review": confidence.get("needs_review", False),
        },
        "redaction": {
            # Fields whose extracted value carries a redaction sentinel. Each is
            # scored 0.0 and is NOT an extracted value.
            "redacted_fields": confidence.get("redacted_fields", []),
            # Credential shapes removed from the document before processing.
            "credentials_removed": sum(redaction_counts.values()),
            "credential_kinds": sorted(redaction_counts),
        },
    }
    return json.dumps(result_doc, ensure_ascii=False, indent=2)


class OutputFormatNode(FunctionNode):
    """Assemble the final structured-JSON extraction result (inner domain node).

    Reads extracted_fields, confidence_scores, and document_data from State,
    renders the structured JSON extraction document, and writes it to
    extraction_result (and result) for the outer PostProcessNode.

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        extracted_fields:  str  — JSON-serialised extracted-field dict (ADR-005)
        confidence_scores: str  — JSON-serialised confidence result (ADR-005)
        document_data:     str  — JSON-serialised document payload (ADR-005)

    Output state keys (partial dict):
        extraction_result: str
        result:            str  (same as extraction_result — backbone convention)
        status:            str
        error_log:         list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        extracted: Dict[str, Any] = from_json(state.get("extracted_fields"), {})
        confidence: Dict[str, Any] = from_json(state.get("confidence_scores"), {})
        document_data: Dict[str, Any] = from_json(state.get("document_data"), {})
        # Carried inside the normalised request across the subgraph boundary and
        # unpacked into document_data by InputValidateNode.
        raw_counts = document_data.get("redaction_counts")
        redaction_counts: Dict[str, int] = raw_counts if isinstance(raw_counts, dict) else {}

        document_id = document_data.get("document_id", "unknown")

        if not extracted:
            logger.error(
                "OutputFormatNode: extracted_fields missing in state for document_id=%s",
                document_id,
            )
            emit_trace_event(
                "output_format_failed",
                {
                    "reason": contracts.REASON_MISSING_EXTRACTED_FIELDS,
                    "document_id": document_id,
                },
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "failure_reason": contracts.REASON_MISSING_EXTRACTED_FIELDS,
                "error_log": [f"OutputFormatNode: extracted_fields missing for document_id={document_id}"],
            }

        # ── Assemble the structured JSON result ───────────────────────────────
        extraction_result = _assemble_result(document_data, extracted, confidence, redaction_counts)

        logger.info(
            "OutputFormatNode: document_id=%s result_chars=%d overall_confidence=%.3f",
            document_id,
            len(extraction_result),
            float(confidence.get("overall_confidence", 0.0)),
        )
        emit_trace_event(
            "output_format_complete",
            {
                "document_id": document_id,
                "result_length": len(extraction_result),
                "field_count": extracted.get("field_count", 0),
                "overall_confidence": confidence.get("overall_confidence", 0.0),
            },
            state,
        )

        return {
            "extraction_result": extraction_result,
            "result": extraction_result,
            "status": AgentStatus.SUCCESS.value,
        }
