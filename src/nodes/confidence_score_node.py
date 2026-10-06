"""AgentCore Platform v1.0"""

# TEL-C2-002 — ConfidenceScoreNode
# Inner domain node 4: compute extraction confidence scores.
#
# Determines a per-field and overall confidence in [0.0, 1.0] for the
# extracted fields, weighted by OCR quality and pattern strength, so that
# downstream consumers and human reviewers know which values to trust.
#
# Scoring model (v1: rule-based):
#   base_field_score = 0.5 + 0.4 * ocr_quality
#   present, well-formed values are boosted; empty categories are penalised.
#   overall_confidence = mean(per-field scores) scaled by ocr_quality.
#
# A REDACTED VALUE IS NOT AN EXTRACTED VALUE.
# The platform's personal-data filter masks name-shaped and identifier-shaped
# text in the caller's document BEFORE any template code runs, and this
# template's own credential redaction runs at the request boundary. Either way
# the sentinel arrives here looking like an ordinary string, so a scorer that
# treats it as ordinary content will certify it. Measured on this repo's own
# sample contract before the fix: the `party` field came back as
# "<operator> and [MASKED] KK" with a per-field confidence of 0.983 — the
# HIGHEST of any field on the document — with needs_review false and an empty
# low-confidence list. The score of a redacted value is now 0.0, the field is
# listed under both low_confidence_fields and redacted_fields, and needs_review
# is forced true.
#
# Inner node — ANONYMOUS trust (review finding 5, corrected 2026-07-02).
# Returns ONLY the state keys this node writes (partial-dict contract).

import logging
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas import contracts
from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)

# Confidence threshold below which a field is flagged for human review.
_REVIEW_THRESHOLD = 0.70


def _is_redacted(value: Any) -> bool:
    """True when *value* — or any element of it — carries a redaction sentinel."""
    if isinstance(value, (list, tuple)):
        return any(_is_redacted(item) for item in value)
    return contracts.contains_redaction_sentinel(value)


def _score_value(value: Any, ocr_quality: float) -> float:
    """Score a single extracted value in [0.0, 1.0].

    A value carrying a redaction sentinel scores 0.0: nothing was extracted
    there, and reporting a confident score over a mask is how a placeholder ends
    up consumed as a fact (see the module docstring).
    """
    base = 0.5 + 0.4 * ocr_quality
    if value is None:
        return 0.0
    if _is_redacted(value):
        return 0.0
    text = str(value).strip()
    if not text:
        return round(base * 0.3, 3)
    # Longer, structured values carry more signal — small length bonus.
    length_bonus = min(len(text) / 200.0, 0.1)
    return round(min(base + length_bonus, 1.0), 3)


def _flatten_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten the nested {category: {key: value}} extracted-fields dict."""
    flat: Dict[str, Any] = {}
    for category, cat_fields in fields.items():
        if not isinstance(cat_fields, dict):
            continue
        for key, value in cat_fields.items():
            flat[f"{category}.{key}"] = value
    return flat


class ConfidenceScoreNode(FunctionNode):
    """Compute per-field and overall extraction confidence for TEL-C2-002.

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        extracted_fields: str  — JSON-serialised extracted-field dict (ADR-005)
        document_data:    str  — JSON-serialised document payload (for ocr_quality)

    Output state keys (partial dict):
        confidence_scores:  str    — JSON-serialised scoring result (ADR-005)
        overall_confidence: float  — overall confidence in [0.0, 1.0]
        status:             str
        error_log:          list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        extracted: Dict[str, Any] = from_json(state.get("extracted_fields"), {})
        document_data: Dict[str, Any] = from_json(state.get("document_data"), {})

        if not extracted:
            logger.error("ConfidenceScoreNode: extracted_fields missing in state")
            emit_trace_event(
                "confidence_score_failed",
                {"reason": contracts.REASON_MISSING_EXTRACTED_FIELDS},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "failure_reason": contracts.REASON_MISSING_EXTRACTED_FIELDS,
                "error_log": ["ConfidenceScoreNode: extracted_fields missing in state"],
            }

        document_id = document_data.get("document_id", "unknown")
        # ocr_quality is derived upstream, but read defensively: a non-finite
        # value here would make every comparison below False, which is the
        # silent fail-open this whole scorer exists to avoid.
        ocr_quality = contracts.finite_in_range(document_data.get("ocr_quality", 0.5), 0.0, 1.0)
        if ocr_quality is None:
            ocr_quality = 0.0

        flat_fields = _flatten_fields(extracted.get("fields", {}))

        per_field: Dict[str, float] = {}
        low_confidence_fields: List[str] = []
        redacted_fields: List[str] = []
        for name, value in flat_fields.items():
            score = _score_value(value, ocr_quality)
            per_field[name] = score
            if _is_redacted(value):
                redacted_fields.append(name)
            if score < _REVIEW_THRESHOLD:
                low_confidence_fields.append(name)

        if per_field:
            overall = round(
                (sum(per_field.values()) / len(per_field)) * (0.6 + 0.4 * ocr_quality),
                3,
            )
        else:
            overall = 0.0

        needs_review = overall < _REVIEW_THRESHOLD or bool(low_confidence_fields) or bool(redacted_fields)

        confidence_scores: Dict[str, Any] = {
            "per_field": per_field,
            "overall_confidence": overall,
            "low_confidence_fields": low_confidence_fields,
            "redacted_fields": redacted_fields,
            "threshold": _REVIEW_THRESHOLD,
            "needs_review": needs_review,
            "ocr_quality": ocr_quality,
        }

        logger.info(
            "ConfidenceScoreNode: document_id=%s overall=%.3f low_conf=%d redacted=%d needs_review=%s",
            document_id,
            overall,
            len(low_confidence_fields),
            len(redacted_fields),
            needs_review,
        )
        emit_trace_event(
            "confidence_score_complete",
            {
                "document_id": document_id,
                "overall_confidence": overall,
                "low_confidence_count": len(low_confidence_fields),
                "redacted_count": len(redacted_fields),
                "needs_review": needs_review,
            },
            state,
        )

        return {
            "confidence_scores": to_json(confidence_scores),
            "overall_confidence": overall,
            "status": AgentStatus.SUCCESS.value,
        }
