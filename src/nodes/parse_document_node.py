"""AgentCore Platform v1.0"""

# TEL-C2-002 — ParseDocumentNode
# Inner domain node 2: OCR pre-processing + document-type detection.
#
# Responsibilities:
#   - Segment the raw OCR/extracted content into normalised text blocks
#   - Detect the document type from content keywords when it is 'unknown'
#   - Estimate an OCR-quality signal from the text (used later for confidence)
#   - Annotate document_data with derived fields for downstream nodes
#
# Inner node — ANONYMOUS trust (review finding 5, corrected 2026-07-02).
# Returns ONLY the state keys this node writes (partial-dict contract).

import logging
import re
from typing import Any, ClassVar, Dict, List, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas import contracts
from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)

# Keyword signatures used to detect a telecom document type from raw content.
# Read-only module constant — NEVER mutated inside execute() (review finding 6).
_DOCUMENT_TYPE_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "service_contract": ("agreement", "party", "term of service", "contract", "契約", "甲", "乙"),
    "service_plan": ("plan", "monthly fee", "data allowance", "tariff", "料金", "プラン"),
    "equipment_spec": ("model", "specification", "hardware", "device", "型番", "仕様"),
    "network_config": ("vlan", "ip address", "subnet", "gateway", "bandwidth", "config"),
    "regulatory_filing": ("ministry", "総務省", "article", "filing", "regulatory", "電気通信事業法"),
}

# A "clean" OCR line is mostly printable word/number characters. A high ratio
# of replacement/garble characters lowers the estimated OCR quality.
_GARBLE_RE = re.compile(r"[^\w\s\.,:;/\-()%&@#'\"’、。「」（）ぁ-んァ-ヶ一-龠]", re.UNICODE)


def _segment_blocks(content: str) -> List[str]:
    """Split raw content into non-empty, stripped text blocks (paragraph/line based).

    Bounded at `contracts.MAX_TEXT_BLOCKS`: the block list is carried in State
    and a document is caller-supplied, so an unbounded segmentation is an
    unbounded allocation driven by the request.
    """
    blocks: List[str] = []
    for chunk in re.split(r"\n\s*\n|\r\n\r\n", content):
        for line in chunk.splitlines():
            stripped = line.strip()
            if stripped:
                blocks.append(stripped)
                if len(blocks) >= contracts.MAX_TEXT_BLOCKS:
                    return blocks
    return blocks


def _detect_document_type(content: str) -> str:
    """Detect a document type from content keywords; return 'unknown' if none match."""
    lowered = content.lower()
    best_type, best_hits = "unknown", 0
    for dtype, keywords in _DOCUMENT_TYPE_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw.lower() in lowered)
        if hits > best_hits:
            best_type, best_hits = dtype, hits
    return best_type if best_hits > 0 else "unknown"


def _estimate_ocr_quality(content: str) -> float:
    """Estimate OCR quality in [0.0, 1.0] from the garble-character ratio.

    Redaction sentinels are removed before the ratio is taken. They are written
    by the platform's personal-data filter and by this template's own credential
    redaction — both AFTER the scan was produced — and their square brackets are
    garble characters under the pattern below. Left in, they make a clean scan
    that happened to mention a person or a token read as a poor one, which turns
    a privacy control into a quality signal. Measured on this repo's own sample
    contract: 0.957 with a sentinel present, 1.0 without.
    """
    if not content:
        return 0.0
    measurable = content
    for sentinel in contracts.REDACTION_SENTINELS:
        measurable = measurable.replace(sentinel, "")
    if not measurable:
        return 0.0
    garble = len(_GARBLE_RE.findall(measurable))
    ratio = garble / max(len(measurable), 1)
    quality = max(0.0, 1.0 - (ratio * 5.0))
    return round(min(quality, 1.0), 3)


class ParseDocumentNode(FunctionNode):
    """OCR pre-processing and document-type detection for TEL-C2-002.

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        document_data: str  — JSON-serialised document payload (ADR-005)

    Output state keys (partial dict):
        document_data: str  — enriched JSON string (ADR-005), same key updated
        status:        str
        error_log:     list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        document_data: Dict[str, Any] = from_json(state.get("document_data"), {})

        if not document_data:
            logger.error("ParseDocumentNode: document_data is empty or missing")
            emit_trace_event(
                "parse_document_failed",
                {"reason": contracts.REASON_MISSING_DOCUMENT_DATA},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "failure_reason": contracts.REASON_MISSING_DOCUMENT_DATA,
                "error_log": ["ParseDocumentNode: document_data missing in state"],
            }

        document_id = document_data.get("document_id", "unknown")
        content = str(document_data.get("content", ""))

        # ── Segment into text blocks ──────────────────────────────────────────
        text_blocks = _segment_blocks(content)

        # ── Document-type detection (only if not already declared) ────────────
        declared_type = document_data.get("document_type", "unknown")
        detected_type = declared_type if declared_type != "unknown" else _detect_document_type(content)

        # ── OCR-quality estimate ──────────────────────────────────────────────
        ocr_quality = _estimate_ocr_quality(content)

        # ── Enrich document_data (local copy — no module-global mutation) ─────
        enriched: Dict[str, Any] = dict(document_data)
        enriched["detected_type"] = detected_type
        enriched["document_type"] = detected_type
        enriched["text_blocks"] = text_blocks
        enriched["block_count"] = len(text_blocks)
        enriched["ocr_quality"] = ocr_quality

        logger.info(
            "ParseDocumentNode: document_id=%s detected_type=%s blocks=%d ocr_quality=%.3f",
            document_id,
            detected_type,
            len(text_blocks),
            ocr_quality,
        )
        emit_trace_event(
            "parse_document_complete",
            {
                "document_id": document_id,
                "detected_type": detected_type,
                "block_count": len(text_blocks),
                "ocr_quality": ocr_quality,
            },
            state,
        )

        return {
            "document_data": to_json(enriched),
            "status": AgentStatus.SUCCESS.value,
        }
