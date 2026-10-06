"""AgentCore Platform v1.0"""

# TEL-C2-002 — ExtractFieldsNode
# Inner domain node 3: extract structured fields from the telecom document.
#
# v1 implementation: deterministic pattern/keyword-based extraction. No language
# model is invoked anywhere in this template — the fields are derived from the
# parsed content with regular expressions and keyword heuristics, which is why
# the manifest declares generation_mode: deterministic.
#
# The runtime configuration's llm.* block reaches the inner graph as
# DomainWorkflowGraph.config["configurable"], but NOTHING reads it. It is a
# declared-but-inactive contract held for a future model-backed revision, and it
# is named as such in the operation guide so an operator tuning temperature is
# not left believing it changed anything.
#
# Field categories extracted (per detected document type):
#   - contract_terms       (service_contract)
#   - service_plan_details (service_plan)
#   - equipment_specs      (equipment_spec)
#   - network_config       (network_config)
#   - regulatory_data      (regulatory_filing)
#   - common               (dates, amounts, identifiers — all document types)
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

# Common cross-document extraction patterns (read-only module constant).
_DATE_RE = re.compile(r"\b(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)\b")
_AMOUNT_RE = re.compile(r"(?:¥|JPY\s?|\$)\s?[\d,]+(?:\.\d+)?|\b[\d,]+\s?(?:円|yen)\b", re.IGNORECASE)
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# Per-type key labels to look for as "label: value" pairs (read-only).
_TYPE_FIELD_LABELS: Dict[str, Tuple[str, ...]] = {
    "service_contract": ("contract number", "effective date", "term", "party", "契約番号", "契約期間"),
    "service_plan": ("plan name", "monthly fee", "data allowance", "プラン名", "月額"),
    "equipment_spec": ("model", "manufacturer", "serial", "firmware", "型番", "製造元"),
    "network_config": ("vlan", "subnet", "gateway", "bandwidth", "hostname"),
    "regulatory_filing": ("article", "filing date", "authority", "reference", "条", "提出日"),
}


def _extract_labeled_pairs(content: str, labels: Tuple[str, ...]) -> Dict[str, str]:
    """Extract `label: value` pairs for the given labels (first match each)."""
    found: Dict[str, str] = {}
    for label in labels:
        pattern = re.compile(rf"{re.escape(label)}\s*[:：=]\s*(.+)", re.IGNORECASE)
        m = pattern.search(content)
        if m:
            found[label.strip().replace(" ", "_").lower()] = m.group(1).strip()[:200]
    return found


def _extract_common_fields(content: str) -> Dict[str, List[str]]:
    """Extract cross-document fields (dates, amounts, IP addresses)."""
    return {
        "dates": sorted(set(_DATE_RE.findall(content)))[:20],
        "amounts": sorted(set(m.strip() for m in _AMOUNT_RE.findall(content)))[:20],
        "ip_addresses": sorted(set(_IP_RE.findall(content)))[:20],
    }


class ExtractFieldsNode(FunctionNode):
    """Extract structured fields from the parsed telecom document.

    v1: deterministic pattern/keyword-based extraction — no language model is
    invoked. The runtime llm.* block is declared-but-inactive (module docstring).

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        document_data: str  — JSON-serialised enriched document payload (ADR-005)

    Output state keys (partial dict):
        extracted_fields: str  — JSON-serialised extracted-field dict (ADR-005)
        status:           str
        error_log:        list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        document_data: Dict[str, Any] = from_json(state.get("document_data"), {})

        if not document_data:
            logger.error("ExtractFieldsNode: document_data missing in state")
            emit_trace_event(
                "extract_fields_failed",
                {"reason": contracts.REASON_MISSING_DOCUMENT_DATA},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "failure_reason": contracts.REASON_MISSING_DOCUMENT_DATA,
                "error_log": ["ExtractFieldsNode: document_data missing in state"],
            }

        document_id = document_data.get("document_id", "unknown")
        document_type = document_data.get("document_type", "unknown")
        content = str(document_data.get("content", ""))

        # ── Common cross-document fields ──────────────────────────────────────
        fields: Dict[str, Any] = {"common": _extract_common_fields(content)}

        # ── Type-specific labelled fields (local copy — no module-global mutation) ─
        labels = _TYPE_FIELD_LABELS.get(document_type, ())
        type_pairs = _extract_labeled_pairs(content, labels) if labels else {}
        category = {
            "service_contract": "contract_terms",
            "service_plan": "service_plan_details",
            "equipment_spec": "equipment_specs",
            "network_config": "network_config",
            "regulatory_filing": "regulatory_data",
        }.get(document_type, "general")
        fields[category] = type_pairs

        # ── Count non-empty extracted values ──────────────────────────────────
        field_count = sum(len(v) for v in fields.values() if isinstance(v, dict)) + sum(
            len(vv) for v in fields.values() if isinstance(v, dict) for vv in v.values() if isinstance(vv, list)
        )

        extracted_fields: Dict[str, Any] = {
            "document_type": document_type,
            "fields": fields,
            "field_count": field_count,
        }

        logger.info(
            "ExtractFieldsNode: document_id=%s type=%s field_count=%d",
            document_id,
            document_type,
            field_count,
        )
        emit_trace_event(
            "extract_fields_complete",
            {
                "document_id": document_id,
                "document_type": document_type,
                "field_count": field_count,
                "categories": sorted(fields.keys()),
            },
            state,
        )

        return {
            "extracted_fields": to_json(extracted_fields),
            "status": AgentStatus.SUCCESS.value,
        }
