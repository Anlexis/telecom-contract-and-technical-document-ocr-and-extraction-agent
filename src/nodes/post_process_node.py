"""AgentCore Platform v1.0"""

# TEL-C2-002 — PostProcessNode
# Outer backbone post_process slot: the output gate over the assembled
# extraction result, and the only place formatted_output / result are written.
#
# The gate scans the assembled result for credential-like patterns before it
# reaches the caller, using the UNION of the framework's own credential
# detector and this template's additional patterns. The union is the point:
#
#   * The framework's set is the FLOOR. It describes credential FORMATS —
#     sk_live_/sk_test_, sk-<20+>, eyJ… (JWT), AKIA<16>, Bearer <16+>, and
#     database connection URIs. A value the framework catches and this gate
#     missed would make the framework raise INSIDE the wrapper, which discards
#     whatever this node returned — so a detector gap here is a containment
#     bypass, not a smaller net.
#
#   * The local patterns catch shapes the framework's do not: `pk-`/`ak-`
#     prefixed keys, short Bearer tokens, and `password = …` style assignments.
#     Delegating to the framework alone would make this gate NARROWER while
#     looking like a tightening.
#
# On a violation the node does not raise, and does not merely overwrite the
# caller-facing strings: it CLEARS every output-bearing field in State.
# AgentBaseGraph.get_output() falls back to state["result"] with no status
# check, so a gate that raises — or that returns ERROR while leaving the
# structured fields in place — still ships the un-gated answer inside the error
# envelope. The replacement value is a constant reason code and is truthy,
# because a FALSY formatted_output re-opens that same fallback.
#
# The gate is a module-level function, not an instance method, and deliberately
# is not named `_security_gate_output` (the framework's @final base method) and
# is not an `_extra_security_gate_*` hook.
#
# Returns ONLY the state keys this node writes (partial-dict contract).

import logging
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from framework.security.credential_detector import detect_credentials
from shared.utils.audit_logger import emit_trace_event

from src.schemas import contracts

logger = logging.getLogger(__name__)

# State fields that can carry released text to the caller. Every one of them is
# blanked on a gate violation.
_OUTPUT_BEARING_FIELDS = (
    "formatted_output",
    "result",
    "extraction_result",
    "extracted_fields",
    "confidence_scores",
)


def _run_output_gate(content: str) -> Optional[str]:
    """Return the first violation KIND found in *content*, or None if clean.

    Union of the framework's credential detector and the template's own
    patterns (see the module docstring). Returns a kind drawn from a closed set;
    the matched text is never returned, logged or audited.
    """
    framework_findings = detect_credentials(content)
    if framework_findings:
        return str(framework_findings[0]["type"])
    for kind, pattern in contracts.LOCAL_CREDENTIAL_PATTERNS:
        if pattern.search(content):
            return kind
    return None


class PostProcessNode(FunctionNode):
    """Apply the output gate and expose the final extraction result.

    Outer backbone post_process slot. Declared ANONYMOUS — trust was already
    enforced at PreProcessNode (VERIFIED_EXTERNAL).

    Input state keys:
        extraction_result: str    — structured-JSON result from OutputFormatNode
        overall_confidence: float — overall extraction confidence

    Output state keys (partial dict):
        formatted_output: str
        result:           str
        status:           str
        failure_reason:   str       — closed-set constant, set only on ERROR
        error_log:        list[str] — internal channel, set only on ERROR
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    @staticmethod
    def _contain(reason: str) -> Dict[str, Any]:
        """ERROR delta that clears every output-bearing field.

        The replacement for ``formatted_output`` is a constant reason code:
        truthy, so the framework's ``formatted_output or result`` fallback does
        not re-open, and drawn from a closed set, so nothing a node authored and
        nothing an exception produced can ride out on it.
        """
        contained: Dict[str, Any] = {field: "" for field in _OUTPUT_BEARING_FIELDS}
        contained["formatted_output"] = f'{{"reason": "{reason}"}}'
        contained["result"] = ""
        contained["overall_confidence"] = 0.0
        contained["status"] = AgentStatus.ERROR.value
        contained["failure_reason"] = reason
        return contained

    def execute(self, state: AgentState) -> Dict[str, Any]:
        extraction_result: str = state.get("extraction_result") or ""

        confidence = contracts.finite_in_range(state.get("overall_confidence") or 0.0, 0.0, 1.0)
        overall_confidence: float = confidence if confidence is not None else 0.0

        # ── No result to publish ──────────────────────────────────────────────
        # Reached only when the pipeline reported success without producing a
        # document. There is nothing to gate and nothing to return, so the
        # request fails closed rather than publishing a placeholder that reads
        # like an answer.
        if not extraction_result.strip():
            logger.warning("PostProcessNode: no extraction result to publish")
            emit_trace_event(
                "post_process_empty_result",
                {"reason": contracts.REASON_MISSING_EXTRACTED_FIELDS},
                state,
            )
            return {
                **self._contain(contracts.REASON_MISSING_EXTRACTED_FIELDS),
                "error_log": ["PostProcessNode: no extraction result to publish"],
            }

        # ── Output gate ───────────────────────────────────────────────────────
        violation = _run_output_gate(extraction_result)
        if violation:
            logger.error("PostProcessNode: output gate blocked the result — %s", violation)
            emit_trace_event(
                "post_process_output_blocked",
                {"violation": violation},
                state,
            )
            return {
                **self._contain(contracts.REASON_OUTPUT_WITHHELD),
                "error_log": [f"PostProcessNode: output gate blocked the result — {violation}"],
            }

        logger.info(
            "PostProcessNode: output gate passed — length=%d overall_confidence=%.2f",
            len(extraction_result),
            overall_confidence,
        )
        emit_trace_event(
            "post_process_complete",
            {
                "output_length": len(extraction_result),
                "overall_confidence": overall_confidence,
            },
            state,
        )

        return {
            "formatted_output": extraction_result,
            "result": extraction_result,
            "status": AgentStatus.SUCCESS.value,
        }


__all__: List[str] = ["PostProcessNode"]
