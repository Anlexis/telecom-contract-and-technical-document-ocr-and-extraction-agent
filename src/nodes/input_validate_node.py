"""AgentCore Platform v1.0"""

# TEL-C2-002 — InputValidateNode
# Inner domain node 1: domain-level validation of the document payload.
#
# Distinct from PreProcessNode (trust + the structural request contract):
# this node applies domain-business rules — field types, document-type
# normalisation, content sizing, and metadata extraction.
#
# It re-applies the SAME bounds as PreProcessNode rather than trusting them.
# The two nodes sit on opposite sides of a subgraph boundary, they are both
# reachable directly, and a contract enforced in only one of them holds only
# for the path that happens to run first. The shared rules live once, in
# src/schemas/contracts.py, so the two cannot drift apart.
#
# Inner node — ANONYMOUS trust (the outer PreProcessNode with VERIFIED_EXTERNAL
# already enforced trust; inner nodes must be ANONYMOUS so the outer
# InvocationContext passes through the GraphNode boundary without rejection).
#
# Returns ONLY the state keys this node writes (partial-dict contract).

import json
import logging
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas import contracts
from src.schemas.state import to_json

logger = logging.getLogger(__name__)

# Known document-type aliases for normalisation (canonical telecom document types).
_DOCUMENT_TYPE_ALIASES: Dict[str, str] = {
    "contract": "service_contract",
    "service contract": "service_contract",
    "service_contract": "service_contract",
    "plan": "service_plan",
    "service plan": "service_plan",
    "service_plan": "service_plan",
    "equipment": "equipment_spec",
    "equipment spec": "equipment_spec",
    "equipment_spec": "equipment_spec",
    "spec": "equipment_spec",
    "network": "network_config",
    "network config": "network_config",
    "network_config": "network_config",
    "config": "network_config",
    "regulatory": "regulatory_filing",
    "regulatory filing": "regulatory_filing",
    "regulatory_filing": "regulatory_filing",
    "filing": "regulatory_filing",
}

# The canonical document types. Normalisation maps every accepted alias onto
# one of these, so `document_type` in the report is a closed set regardless of
# what the caller sent.
CANONICAL_DOCUMENT_TYPES = frozenset(set(_DOCUMENT_TYPE_ALIASES.values()) | {"unknown"})


def _normalise_document_type(raw_type: Any) -> str:
    """Normalise the declared document_type; return 'unknown' if unrecognised/absent."""
    if not raw_type:
        return "unknown"
    return _DOCUMENT_TYPE_ALIASES.get(str(raw_type).lower().strip(), "unknown")


class InputValidateNode(FunctionNode):
    """Domain validation of the document payload for TEL-C2-002.

    Applies business-rule checks beyond the structural request contract in
    PreProcessNode: field types, document-type normalisation, content sizing,
    page/language/source metadata extraction.

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        validated_input: str  — normalised JSON string from PreProcessNode
                                Falls back to user_input when called directly.

    Output state keys (partial dict):
        document_data:  str   — JSON-serialised normalised document payload (ADR-005)
        status:         str
        failure_reason: str   — closed-set constant, set only on ERROR
        error_log:      list[str]  — internal channel, set only on ERROR
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    @staticmethod
    def _reject(state: AgentState, reason: str, detail: str, audit: Dict[str, Any]) -> Dict[str, Any]:
        """ERROR delta carrying a closed-set reason; never a rejected value."""
        emit_trace_event("input_validate_failed", {"reason": reason, **audit}, state)
        return {
            "status": AgentStatus.ERROR.value,
            "failure_reason": reason,
            "error_log": [f"InputValidateNode: {detail}"],
        }

    def execute(self, state: AgentState) -> Dict[str, Any]:
        raw = state.get("validated_input") or state.get("user_input", "")

        if not isinstance(raw, str) or not raw.strip():
            return self._reject(state, contracts.REASON_EMPTY_INPUT, "no input to validate", {})
        if len(raw) > contracts.MAX_PAYLOAD_CHARS:
            return self._reject(
                state,
                contracts.REASON_PAYLOAD_TOO_LARGE,
                f"payload exceeds {contracts.MAX_PAYLOAD_CHARS} characters",
                {"limit": contracts.MAX_PAYLOAD_CHARS},
            )

        # ── Parse ─────────────────────────────────────────────────────────────
        try:
            payload: Dict[str, Any] = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            # The exception text quotes the caller's own bytes; it is not carried.
            logger.error("InputValidateNode: payload is not valid JSON")
            return self._reject(state, contracts.REASON_MALFORMED_JSON, "payload is not valid JSON", {})

        if not isinstance(payload, dict):
            return self._reject(state, contracts.REASON_NOT_AN_OBJECT, "payload is not a JSON object", {})

        tokens = contracts.find_control_tokens(payload)
        if tokens:
            logger.error("InputValidateNode: control-token delimiters present in payload")
            return self._reject(
                state,
                contracts.REASON_CONTROL_TOKEN,
                f"payload carries {len(tokens)} chat-template control token(s)",
                {"token_count": len(tokens)},
            )

        # ── Inert metadata — echoed verbatim into the report ──────────────────
        document_id = payload.get("document_id")
        if not contracts.is_inert_identifier(document_id):
            return self._reject(
                state,
                contracts.REASON_INVALID_IDENTIFIER,
                "metadata field 'document_id' is missing or not an accepted identifier",
                {"field": "document_id"},
            )
        source = payload.get("source", "unknown")
        if not contracts.is_inert_identifier(source):
            return self._reject(
                state,
                contracts.REASON_INVALID_IDENTIFIER,
                "metadata field 'source' is not an accepted identifier",
                {"field": "source"},
            )
        language = payload.get("language", "und")
        if not contracts.is_language_tag(language):
            return self._reject(
                state,
                contracts.REASON_INVALID_IDENTIFIER,
                "metadata field 'language' is not an accepted language tag",
                {"field": "language"},
            )

        # ── Content ───────────────────────────────────────────────────────────
        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            return self._reject(state, contracts.REASON_EMPTY_CONTENT, "document content is empty", {})
        if len(content) > contracts.MAX_CONTENT_CHARS:
            return self._reject(
                state,
                contracts.REASON_CONTENT_TOO_LARGE,
                f"document content exceeds {contracts.MAX_CONTENT_CHARS} characters",
                {"limit": contracts.MAX_CONTENT_CHARS},
            )

        # ── Caller-controlled numerics: finite and bounded, fail CLOSED ───────
        pages_value = contracts.finite_in_range(payload.get("pages", 0), contracts.MIN_PAGES, contracts.MAX_PAGES)
        if pages_value is None:
            return self._reject(
                state,
                contracts.REASON_INVALID_NUMBER,
                "numeric field 'pages' is not a finite value within the accepted range",
                {"field": "pages"},
            )

        # ── Normalise document type + metadata ────────────────────────────────
        document_type = _normalise_document_type(payload.get("document_type"))

        # The redaction tally the request boundary attached (see contracts
        # .REDACTION_KEY). Validated the same way as any other value: a
        # non-mapping, or anything but non-negative integer counts, is dropped
        # rather than carried into the report.
        raw_counts = payload.get(contracts.REDACTION_KEY)
        redaction_counts: Dict[str, int] = {}
        if isinstance(raw_counts, dict):
            redaction_counts = {
                str(kind): int(count)
                for kind, count in raw_counts.items()
                if isinstance(count, int) and not isinstance(count, bool) and count >= 0
            }

        document_data: Dict[str, Any] = {
            "document_id": document_id,
            "document_type": document_type,
            "content": content,
            "content_length": len(content),
            "pages": int(pages_value),
            "language": language,
            "source": source,
            "redaction_counts": redaction_counts,
        }

        logger.info(
            "InputValidateNode: document_id=%s type=%s content_len=%d pages=%d",
            document_id,
            document_type,
            len(content),
            int(pages_value),
        )
        emit_trace_event(
            "input_validate_complete",
            {
                "document_id": document_id,
                "document_type": document_type,
                "content_length": len(content),
                "pages": int(pages_value),
            },
            state,
        )

        return {
            "document_data": to_json(document_data),
            "status": AgentStatus.SUCCESS.value,
        }


__all__: List[str] = ["InputValidateNode", "CANONICAL_DOCUMENT_TYPES"]
