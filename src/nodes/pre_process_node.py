"""AgentCore Platform v1.0"""

# TEL-C2-002 — PreProcessNode
# Outer backbone pre_process slot: trust gate + the caller-data contract.
#
# Responsibilities:
#   - Enforce VERIFIED_EXTERNAL trust (the required_trust_level declaration)
#   - Bound the request structurally before anything parses it
#   - Reject empty / non-JSON input early (fail-fast)
#   - Screen for chat-template control tokens over keys and values, raw and
#     markup-stripped
#   - Redact credential shapes out of the document body (see below)
#   - Validate every caller-controlled number through a finite+bounded parser
#   - Lock the metadata fields that are echoed into the report to inert shapes
#   - Write validated_input (normalised JSON string) + enriched_context to State
#   - Emit an audit event for every validation decision
#
# Why credentials are REDACTED here rather than refused:
# the framework's final output gate scans every value of every node result, and
# this node's result carries the whole normalised payload. A credential-shaped
# string anywhere in the caller's document therefore makes the very first node
# fail with a traceback and nothing the caller can act on. Network configuration
# sheets — a document type this agent explicitly accepts — routinely carry
# `password = …`. Removing the secret from the text keeps the document
# processable and the secret out of every downstream surface; the caller is told
# how many values were redacted and of what kind, never what they were.
#
# Every caller-visible failure is a constant from `contracts.FAILURE_REASONS`.
# Rejected values are never echoed — the field NAME is named, the value is not.
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

# Required top-level keys for a valid document payload.
# document_id is the mandatory identifier; content is the OCR/extracted text
# that the extraction pipeline operates on.
_REQUIRED_DOCUMENT_KEYS = frozenset(
    {
        "document_id",
        "content",
    }
)

# Metadata fields echoed verbatim into the extraction report, with the shape
# each must satisfy. `content` is deliberately absent: extracting its text is
# what this agent exists to do, so it cannot be locked to an identifier.
_INERT_METADATA_CHECKS = (
    ("document_id", contracts.is_inert_identifier),
    ("source", contracts.is_inert_identifier),
    ("language", contracts.is_language_tag),
)


class PreProcessNode(FunctionNode):
    """Caller-data contract for TEL-C2-002.

    Validates the caller-supplied document payload before the domain workflow
    runs. This is the outer backbone's pre_process slot — the only node with
    VERIFIED_EXTERNAL trust, so unauthenticated callers are rejected here
    (fail-fast; inner domain nodes carry ANONYMOUS trust and never see
    untrusted input directly).

    Input state keys:
        user_input: str  — caller-supplied JSON document payload

    Output state keys (partial dict):
        validated_input:  str        — normalised JSON string (re-serialised)
        enriched_context: str        — JSON-serialised channel metadata (ADR-005)
        redaction_counts: str        — JSON-serialised {kind: count} (ADR-005)
        status:           str        — AgentStatus.SUCCESS or ERROR
        failure_reason:   str        — closed-set constant, set only on ERROR
        error_log:        list[str]  — internal channel, set only on ERROR
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    # ── Failure helper ────────────────────────────────────────────────────────

    @staticmethod
    def _reject(state: AgentState, reason: str, detail: str, audit: Dict[str, Any]) -> Dict[str, Any]:
        """Build the ERROR delta for a rejected request.

        `reason` is a closed-set constant and is the only failure information
        the caller receives. `detail` stays in error_log, which is the internal
        channel, and names fields rather than quoting their values.
        """
        emit_trace_event("pre_process_validation_failed", {"reason": reason, **audit}, state)
        return {
            "status": AgentStatus.ERROR.value,
            "failure_reason": reason,
            "error_log": [f"PreProcessNode: {detail}"],
        }

    # ── Node contract ─────────────────────────────────────────────────────────

    def execute(self, state: AgentState) -> Dict[str, Any]:
        user_input = state.get("user_input", "")
        input_context = state.get("input_context", {}) or {}

        # ── Emptiness ─────────────────────────────────────────────────────────
        if not user_input or not isinstance(user_input, str) or not user_input.strip():
            logger.warning("PreProcessNode: user_input is empty or missing")
            return self._reject(
                state,
                contracts.REASON_EMPTY_INPUT,
                "user_input is empty or missing",
                {},
            )

        # ── Structural size bound, before anything parses it ──────────────────
        if len(user_input) > contracts.MAX_PAYLOAD_CHARS:
            logger.warning("PreProcessNode: request body over the accepted bound")
            return self._reject(
                state,
                contracts.REASON_PAYLOAD_TOO_LARGE,
                f"request body exceeds {contracts.MAX_PAYLOAD_CHARS} characters",
                {"limit": contracts.MAX_PAYLOAD_CHARS},
            )

        # ── JSON parse ────────────────────────────────────────────────────────
        try:
            payload: Dict[str, Any] = json.loads(user_input.strip())
        except (json.JSONDecodeError, ValueError):
            # The exception text quotes the caller's own bytes — it stays out of
            # every channel, internal one included.
            logger.warning("PreProcessNode: request body is not valid JSON")
            return self._reject(
                state,
                contracts.REASON_MALFORMED_JSON,
                "request body is not valid JSON",
                {},
            )

        if not isinstance(payload, dict):
            return self._reject(
                state,
                contracts.REASON_NOT_AN_OBJECT,
                "JSON root must be an object",
                {},
            )

        # Reserved names are this node's own channel into the inner graph.
        # Dropping any caller-supplied one first means a caller cannot forge a
        # redaction tally, and no unknown reserved key rides through.
        payload = {
            k: v for k, v in payload.items() if not (isinstance(k, str) and k.startswith(contracts.RESERVED_KEY_PREFIX))
        }

        if len(payload) > contracts.MAX_PAYLOAD_KEYS:
            return self._reject(
                state,
                contracts.REASON_TOO_MANY_KEYS,
                f"request carries more than {contracts.MAX_PAYLOAD_KEYS} top-level keys",
                {"limit": contracts.MAX_PAYLOAD_KEYS},
            )

        # ── Control-token screen — keys and values, raw and markup-stripped ────
        tokens = contracts.find_control_tokens(payload)
        if tokens:
            logger.warning("PreProcessNode: control-token delimiters present in request")
            return self._reject(
                state,
                contracts.REASON_CONTROL_TOKEN,
                f"request carries {len(tokens)} chat-template control token(s)",
                {"token_count": len(tokens)},
            )

        # ── Required fields ───────────────────────────────────────────────────
        missing = _REQUIRED_DOCUMENT_KEYS - payload.keys()
        if missing:
            return self._reject(
                state,
                contracts.REASON_MISSING_FIELDS,
                f"missing required fields: {sorted(missing)}",
                {"missing": sorted(missing)},
            )

        # ── Inert metadata — these are echoed into the report verbatim ────────
        for field, is_valid in _INERT_METADATA_CHECKS:
            if field not in payload:
                continue
            if not is_valid(payload.get(field)):
                logger.warning("PreProcessNode: metadata field %s is not an inert identifier", field)
                return self._reject(
                    state,
                    contracts.REASON_INVALID_IDENTIFIER,
                    f"metadata field '{field}' is not an accepted identifier",
                    {"field": field},
                )

        # ── Caller-controlled numerics: finite and bounded, fail CLOSED ───────
        if "pages" in payload:
            pages = contracts.finite_in_range(payload.get("pages"), contracts.MIN_PAGES, contracts.MAX_PAGES)
            if pages is None:
                logger.warning("PreProcessNode: numeric field pages is not finite and in range")
                return self._reject(
                    state,
                    contracts.REASON_INVALID_NUMBER,
                    "numeric field 'pages' is not a finite value within the accepted range",
                    {"field": "pages"},
                )
            payload["pages"] = int(pages)

        # ── Content ───────────────────────────────────────────────────────────
        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            return self._reject(
                state,
                contracts.REASON_EMPTY_CONTENT,
                "document content is empty",
                {},
            )
        if len(content) > contracts.MAX_CONTENT_CHARS:
            return self._reject(
                state,
                contracts.REASON_CONTENT_TOO_LARGE,
                f"document content exceeds {contracts.MAX_CONTENT_CHARS} characters",
                {"limit": contracts.MAX_CONTENT_CHARS},
            )

        # ── Credential redaction (see the module docstring) ───────────────────
        redacted_payload, redaction_counts = contracts.redact_credentials(payload)

        # ── Success ───────────────────────────────────────────────────────────
        document_id = str(redacted_payload.get("document_id", "unknown"))
        # The tally rides inside the normalised request because that is the only
        # channel that crosses into the inner graph — subgraph.invoke() builds a
        # fresh initial state and no outer State field reaches it.
        redacted_payload[contracts.REDACTION_KEY] = redaction_counts
        normalised_json = json.dumps(redacted_payload, ensure_ascii=False)

        if redaction_counts:
            logger.info(
                "PreProcessNode: redacted %d credential-shaped value(s) before processing",
                sum(redaction_counts.values()),
            )
            emit_trace_event(
                "pre_process_credentials_redacted",
                {
                    "document_id": document_id,
                    # Kinds and counts only — the matched text never leaves here.
                    "kinds": sorted(redaction_counts),
                    "redacted_count": sum(redaction_counts.values()),
                },
                state,
            )

        logger.info(
            "PreProcessNode: validated document_id=%s payload_keys=%d",
            document_id,
            len(redacted_payload),
        )
        emit_trace_event(
            "pre_process_validated",
            {
                "document_id": document_id,
                "payload_keys": sorted(str(k) for k in redacted_payload),
            },
            state,
        )

        channel = input_context.get("channel") if isinstance(input_context, dict) else None
        safe_channel = channel if contracts.is_inert_identifier(channel) else "unknown"

        return {
            "validated_input": normalised_json,
            "enriched_context": to_json(
                {
                    "source": "TelecomContractDocumentOCRAgent",
                    "channel": safe_channel,
                    "document_id": document_id,
                }
            ),
            "redaction_counts": to_json(redaction_counts) if redaction_counts else None,
            "status": AgentStatus.SUCCESS.value,
        }


__all__: List[str] = ["PreProcessNode"]
