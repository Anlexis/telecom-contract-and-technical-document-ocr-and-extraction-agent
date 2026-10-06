# TEL-C2-002 — Unit Tests: domain nodes + graph wiring
#
# Real, non-stub unit tests. They import the REAL modules merged to develop in
# Wave-1 and assert real behaviour (document validation, document-type
# normalisation / detection, field extraction, confidence scoring, the S-3
# output gate, S-1 trust levels, and the Cat 2 two-layer graph composition).
#
# S-4 audit events are patched at the node MODULE level (not via a sys.modules
# stub, which would break the real `shared` package the framework loads at import
# time). Patch pattern per node:
#     monkeypatch.setattr("src.nodes.<mod>.emit_trace_event", lambda *a, **k: None)

import json

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from src.schemas import contracts
from src.schemas.state import from_json, to_json
from tests import _credential_shapes as shapes


# ── Shared fixtures / helpers ─────────────────────────────────────────────────

_CONTENT = (
    "SERVICE CONTRACT\n\n"
    "Contract Number: SB-5G-2026-0042\n"
    "Effective Date: 2026-07-01\n"
    "Party: ACME TELECOM KK and BETA NETWORKS KK\n"
    "Term: 24 months\n"
    "Monthly Fee: 12,800 yen\n\n"
    "This agreement covers 5G NR bearer service and VoLTE for the Kanto region. "
    "The parties agree to the terms of service set out in the attached schedule."
)


def _document_payload(**overrides) -> dict:
    """A complete, valid raw document payload (as a caller would POST)."""
    payload = {
        "document_id": "TEL-DOC-20260712-042",
        "document_type": "service contract",
        "content": _CONTENT,
        "pages": 3,
        "language": "en",
        "source": "ocr_pipeline",
    }
    payload.update(overrides)
    return payload


VALID_PAYLOAD = json.dumps(_document_payload())


def _document_data(**overrides) -> dict:
    """The normalised document_data dict shape produced by InputValidateNode
    (i.e. the input the downstream inner nodes consume)."""
    data = {
        "document_id": "TEL-DOC-20260712-042",
        "document_type": "service_contract",
        "content": _CONTENT,
        "content_length": len(_CONTENT),
        "pages": 3,
        "language": "en",
        "source": "ocr_pipeline",
    }
    data.update(overrides)
    return data


def _extracted_fields(**overrides) -> dict:
    """The extracted-fields dict shape produced by ExtractFieldsNode."""
    data = {
        "document_type": "service_contract",
        "fields": {
            "common": {
                "dates": ["2026-07-01"],
                "amounts": ["12,800 yen"],
                "ip_addresses": [],
            },
            "contract_terms": {
                "contract_number": "SB-5G-2026-0042",
                "term": "24 months",
                "party": "ACME TELECOM KK and BETA NETWORKS KK",
            },
        },
        "field_count": 6,
    }
    data.update(overrides)
    return data


def _confidence_scores(**overrides) -> dict:
    """The confidence-scores dict shape produced by ConfidenceScoreNode."""
    data = {
        "per_field": {"contract_terms.contract_number": 0.9, "contract_terms.term": 0.86},
        "overall_confidence": 0.86,
        "low_confidence_fields": [],
        "threshold": 0.70,
        "needs_review": False,
        "ocr_quality": 0.95,
    }
    data.update(overrides)
    return data


# ── PreProcessNode (outer pre_process, S-1 VERIFIED_EXTERNAL) ──────────────────


class TestPreProcessNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.pre_process_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.pre_process_node import PreProcessNode

        self.node = PreProcessNode()

    def test_valid_payload_returns_success(self):
        result = self.node.execute({"user_input": VALID_PAYLOAD, "input_context": {}})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["validated_input"] is not None
        assert json.loads(result["validated_input"])["document_id"] == "TEL-DOC-20260712-042"

    def test_enriched_context_carries_document_id(self):
        result = self.node.execute({"user_input": VALID_PAYLOAD, "input_context": {"channel": "ocr"}})
        ctx = from_json(result["enriched_context"])
        assert ctx["document_id"] == "TEL-DOC-20260712-042"
        assert ctx["channel"] == "ocr"

    def test_empty_input_returns_error(self):
        result = self.node.execute({"user_input": "", "input_context": {}})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("empty" in e for e in result["error_log"])

    def test_invalid_json_returns_error(self):
        result = self.node.execute({"user_input": "{not valid json}", "input_context": {}})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("JSON" in e or "json" in e for e in result["error_log"])

    def test_non_object_json_returns_error(self):
        result = self.node.execute({"user_input": "[1, 2, 3]", "input_context": {}})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("object" in e for e in result["error_log"])

    def test_missing_required_field_returns_error(self):
        payload = {"document_id": "X"}  # no 'content'
        result = self.node.execute({"user_input": json.dumps(payload), "input_context": {}})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("content" in e for e in result["error_log"])

    def test_empty_content_returns_error(self):
        payload = _document_payload(content="   ")
        result = self.node.execute({"user_input": json.dumps(payload), "input_context": {}})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("content" in e for e in result["error_log"])

    def test_trust_level_is_verified_external(self):
        assert self.node.required_trust_level == TrustLevel.VERIFIED_EXTERNAL

    def test_execute_signature_is_state_first(self):
        import inspect
        from src.nodes.pre_process_node import PreProcessNode

        params = list(inspect.signature(PreProcessNode.execute).parameters.keys())
        assert params[0] == "self" and params[1] == "state"
        assert "_invoke_impl" not in PreProcessNode.__dict__


# ── InputValidateNode (inner domain node 1, ANONYMOUS) ─────────────────────────


class TestInputValidateNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.input_validate_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.input_validate_node import InputValidateNode

        self.node = InputValidateNode()

    def test_valid_input_builds_document_data(self):
        result = self.node.execute({"validated_input": VALID_PAYLOAD})
        assert result["status"] == AgentStatus.SUCCESS.value
        data = from_json(result["document_data"])
        assert data["document_id"] == "TEL-DOC-20260712-042"
        assert data["document_type"] == "service_contract"
        assert data["content_length"] == len(_CONTENT)
        assert data["pages"] == 3

    def test_document_type_alias_normalised(self):
        payload = _document_payload(document_type="plan")
        result = self.node.execute({"validated_input": json.dumps(payload)})
        data = from_json(result["document_data"])
        assert data["document_type"] == "service_plan"

    def test_unrecognised_document_type_is_unknown(self):
        payload = _document_payload(document_type="zzz-not-a-real-type")
        result = self.node.execute({"validated_input": json.dumps(payload)})
        data = from_json(result["document_data"])
        assert data["document_type"] == "unknown"

    def test_falls_back_to_user_input(self):
        result = self.node.execute({"user_input": VALID_PAYLOAD})
        assert result["status"] == AgentStatus.SUCCESS.value

    def test_empty_document_id_returns_error(self):
        payload = _document_payload(document_id="")
        result = self.node.execute({"validated_input": json.dumps(payload)})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("document_id" in e for e in result["error_log"])

    def test_empty_content_returns_error(self):
        payload = _document_payload(content="   ")
        result = self.node.execute({"validated_input": json.dumps(payload)})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("content" in e for e in result["error_log"])

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── ParseDocumentNode (inner domain node 2, ANONYMOUS) ─────────────────────────


class TestParseDocumentNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.parse_document_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.parse_document_node import ParseDocumentNode

        self.node = ParseDocumentNode()

    def _run(self, **doc_overrides):
        state = {"document_data": to_json(_document_data(**doc_overrides))}
        return self.node.execute(state)

    def test_detects_type_when_unknown(self):
        data = from_json(self._run(document_type="unknown")["document_data"])
        assert data["detected_type"] == "service_contract"
        assert data["document_type"] == "service_contract"

    def test_preserves_declared_type(self):
        data = from_json(self._run(document_type="service_plan")["document_data"])
        assert data["detected_type"] == "service_plan"

    def test_segments_text_blocks(self):
        data = from_json(self._run()["document_data"])
        assert data["block_count"] > 0
        assert isinstance(data["text_blocks"], list) and data["text_blocks"]

    def test_estimates_ocr_quality(self):
        data = from_json(self._run()["document_data"])
        assert 0.0 <= data["ocr_quality"] <= 1.0
        # A clean, mostly-ASCII document should score well.
        assert data["ocr_quality"] > 0.5

    def test_missing_document_data_returns_error(self):
        result = self.node.execute({})
        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── ExtractFieldsNode (inner domain node 3, ANONYMOUS) ─────────────────────────


class TestExtractFieldsNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.extract_fields_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.extract_fields_node import ExtractFieldsNode

        self.node = ExtractFieldsNode()

    def test_extracts_common_and_type_fields(self):
        state = {"document_data": to_json(_document_data())}
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        extracted = from_json(result["extracted_fields"])
        assert extracted["document_type"] == "service_contract"
        fields = extracted["fields"]
        assert "common" in fields
        assert "contract_terms" in fields
        assert isinstance(extracted["field_count"], int)
        # Labelled contract fields extracted from the content.
        assert fields["contract_terms"].get("contract_number") == "SB-5G-2026-0042"
        # Common cross-document dates captured.
        assert "2026-07-01" in fields["common"]["dates"]

    def test_general_category_for_unknown_type(self):
        state = {"document_data": to_json(_document_data(document_type="unknown"))}
        extracted = from_json(self.node.execute(state)["extracted_fields"])
        assert "general" in extracted["fields"]

    def test_missing_document_data_returns_error(self):
        result = self.node.execute({})
        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── ConfidenceScoreNode (inner domain node 4, ANONYMOUS) ───────────────────────


class TestConfidenceScoreNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.confidence_score_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.confidence_score_node import ConfidenceScoreNode

        self.node = ConfidenceScoreNode()

    def test_scores_within_range(self):
        state = {
            "extracted_fields": to_json(_extracted_fields()),
            "document_data": to_json(_document_data(ocr_quality=0.95)),
        }
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert isinstance(result["overall_confidence"], float)
        assert 0.0 <= result["overall_confidence"] <= 1.0
        scores = from_json(result["confidence_scores"])
        assert scores["per_field"]  # non-empty
        assert scores["threshold"] == 0.70
        assert isinstance(scores["needs_review"], bool)

    def test_low_quality_flags_needs_review(self):
        # A single short field with zero OCR quality scores below threshold.
        extracted = _extracted_fields(fields={"contract_terms": {"term": "X"}}, field_count=1)
        state = {
            "extracted_fields": to_json(extracted),
            "document_data": to_json(_document_data(ocr_quality=0.0)),
        }
        result = self.node.execute(state)
        scores = from_json(result["confidence_scores"])
        assert scores["needs_review"] is True
        assert result["overall_confidence"] < 0.70

    def test_empty_fields_zero_overall(self):
        extracted = _extracted_fields(fields={}, field_count=0)
        state = {
            "extracted_fields": to_json(extracted),
            "document_data": to_json(_document_data()),
        }
        result = self.node.execute(state)
        assert result["overall_confidence"] == 0.0

    def test_missing_extracted_fields_returns_error(self):
        result = self.node.execute({})
        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── OutputFormatNode (inner domain node 5, ANONYMOUS) ──────────────────────────


class TestOutputFormatNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.output_format_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.output_format_node import OutputFormatNode

        self.node = OutputFormatNode()

    def _state(self):
        return {
            "extracted_fields": to_json(_extracted_fields()),
            "confidence_scores": to_json(_confidence_scores()),
            "document_data": to_json(_document_data(detected_type="service_contract", ocr_quality=0.95)),
        }

    def test_assembles_structured_result(self):
        result = self.node.execute(self._state())
        assert result["status"] == AgentStatus.SUCCESS.value
        extraction_result = result["extraction_result"]
        assert result["result"] == extraction_result
        doc = json.loads(extraction_result)
        assert set(doc.keys()) == {
            "document",
            "extracted_fields",
            "field_count",
            "confidence",
            "redaction",
        }
        assert doc["document"]["document_id"] == "TEL-DOC-20260712-042"
        assert doc["confidence"]["overall_confidence"] == 0.86
        assert doc["redaction"] == {
            "redacted_fields": [],
            "credentials_removed": 0,
            "credential_kinds": [],
        }

    def test_missing_extracted_fields_returns_error(self):
        result = self.node.execute({"document_data": to_json(_document_data())})
        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── PostProcessNode (outer post_process, S-3 output gate, ANONYMOUS) ───────────


class TestPostProcessNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.post_process_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.post_process_node import PostProcessNode

        self.node = PostProcessNode()

    def test_clean_result_passes_gate(self):
        result_doc = '{"document": {"document_id": "TEL-DOC-1"}, "field_count": 3}'
        result = self.node.execute({"extraction_result": result_doc, "overall_confidence": 0.86})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["formatted_output"] == result_doc
        assert result["result"] == result_doc

    def test_empty_result_fails_closed(self):
        """No document produced means the request failed — never a placeholder.

        The previous contract returned SUCCESS here with the text "No extraction
        result generated" as the answer, so a caller whose pipeline had failed
        upstream received a 200 and a sentence. It now fails closed with a
        closed-set reason.
        """
        result = self.node.execute({"extraction_result": "", "overall_confidence": 0.0})
        assert result["status"] == AgentStatus.ERROR.value
        assert result["failure_reason"] in contracts.FAILURE_REASONS
        assert result["result"] == ""
        assert "No extraction result generated" not in json.dumps(result)

    def test_output_gate_withholds_and_clears_every_output_field(self):
        secret = shapes.openai_key()
        leaky = json.dumps({"document": {"note": "token=" + secret}})
        result = self.node.execute(
            {
                "extraction_result": leaky,
                "extracted_fields": to_json(_extracted_fields()),
                "confidence_scores": to_json(_confidence_scores()),
                "overall_confidence": 0.5,
            }
        )
        assert result["status"] == AgentStatus.ERROR.value
        assert result["failure_reason"] in contracts.FAILURE_REASONS
        # Nothing of the blocked answer survives anywhere in the returned mapping.
        assert secret not in json.dumps(result)
        for field in ("result", "extraction_result", "extracted_fields", "confidence_scores"):
            assert result[field] == "", field
        # formatted_output must stay TRUTHY: a falsy value re-opens the
        # framework's `formatted_output or result` fallback.
        assert result["formatted_output"]
        assert json.loads(result["formatted_output"]) == {"reason": contracts.REASON_OUTPUT_WITHHELD}

    def test_output_gate_is_the_union_of_framework_and_local_patterns(self):
        from src.nodes.post_process_node import _run_output_gate

        # Shapes the framework's own detector carries.
        for value in (shapes.openai_key(), shapes.aws_key(), shapes.jwt(), shapes.db_uri(), shapes.bearer_header()):
            assert _run_output_gate(value) is not None, value[:8]
        # Shapes ONLY the local set carries — dropping these to "delegate" to
        # the framework would make this gate narrower while looking tighter.
        for value in (
            shapes.prefixed_key("pk"),
            shapes.prefixed_key("ak"),
            shapes.short_bearer(),
            shapes.password_assignment(),
        ):
            assert _run_output_gate(value) is not None, value[:8]
        # Ordinary telecom prose is untouched. "bearer" is a term of art in this
        # domain (5G NR bearer, EPS bearer), so a rule that fired on it would
        # refuse real documents — and this repo's own sample contract contains
        # exactly that phrase.
        assert _run_output_gate("A perfectly clean extraction result.") is None
        assert _run_output_gate("covers 5G NR bearer service and VoLTE") is None
        assert _run_output_gate("default bearer services are included") is None
        assert _run_output_gate(_CONTENT) is None

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── Graph wiring: outer AgentBaseGraph + inner BaseGraph (Cat 2 nested) ─────────


class TestOuterGraphComposition:
    def test_registers_five_backbone_slots(self):
        from src.graph.graph import (
            DocumentExtractionGraphNode,
            TelecomContractDocumentOCRAgent,
        )
        from src.nodes.post_process_node import PostProcessNode
        from src.nodes.pre_process_node import PreProcessNode

        agent = TelecomContractDocumentOCRAgent()
        agent.compile()
        assert set(agent._nodes.keys()) == {
            "initialize",
            "pre_process",
            "main",
            "post_process",
            "finalize",
        }
        assert isinstance(agent._nodes["pre_process"], PreProcessNode)
        assert isinstance(agent._nodes["main"], DocumentExtractionGraphNode)
        assert isinstance(agent._nodes["post_process"], PostProcessNode)

    def test_name_and_state_schema(self):
        from src.schemas.state import State
        from src.graph.graph import TelecomContractDocumentOCRAgent

        agent = TelecomContractDocumentOCRAgent()
        assert agent.name == "TelecomContractDocumentOCRAgent"
        assert agent.state_schema is State

    def test_graph_alias_matches_real_class(self):
        from src.graph.graph import Graph, TelecomContractDocumentOCRAgent

        assert Graph is TelecomContractDocumentOCRAgent

    def test_main_slot_graphnode_contracts(self):
        from src.graph.graph import DocumentExtractionGraphNode

        node = DocumentExtractionGraphNode()
        assert node.error_strategy == "propagate"
        assert node.propagate_hitl is False
        # extract_input prefers validated_input, falls back to user_input
        assert node.extract_input({"validated_input": "V", "user_input": "U"}) == "V"
        assert node.extract_input({"user_input": "U"}) == "U"

    def test_merge_output_maps_subresult_keys(self):
        from src.graph.graph import DocumentExtractionGraphNode

        node = DocumentExtractionGraphNode()
        sub_result = {
            "extraction_result": "RESULT",
            "extracted_fields": "{}",
            "confidence_scores": "{}",
            "overall_confidence": 0.86,
            "status": AgentStatus.SUCCESS.value,
            "node_history": ["x"],  # not forwarded by merge_output
        }
        delta = node.merge_output({}, sub_result)
        assert delta["extraction_result"] == "RESULT"
        assert delta["overall_confidence"] == 0.86
        assert delta["status"] == AgentStatus.SUCCESS.value
        assert set(delta.keys()) == {
            "extraction_result",
            "extracted_fields",
            "confidence_scores",
            "overall_confidence",
            "status",
        }


class TestInnerDomainGraph:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        for mod in (
            "input_validate_node",
            "parse_document_node",
            "extract_fields_node",
            "confidence_score_node",
            "output_format_node",
        ):
            monkeypatch.setattr(f"src.nodes.{mod}.emit_trace_event", lambda *a, **k: None)

    def test_registers_five_domain_nodes(self):
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        g = DomainWorkflowGraph()
        g.register_nodes()
        assert set(g._nodes.keys()) == {
            "input_validate",
            "parse_document",
            "extract_fields",
            "confidence_score",
            "output_format",
        }

    def test_name_and_state_schema(self):
        from src.schemas.state import State
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        g = DomainWorkflowGraph()
        assert g.name == "tel_c2_002_document_extraction_workflow"
        assert g.state_schema is State

    def test_inner_graph_invoke_produces_result(self):
        """Standalone inner-graph invoke (ANONYMOUS caller) runs the linear pipeline
        and shapes the get_output() dict consumed by the outer merge_output()."""
        from framework.schemas.invocation_context import InvocationContext, TrustLevel
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        g = DomainWorkflowGraph()
        g.compile()
        ctx = InvocationContext(caller_trust_level=TrustLevel.ANONYMOUS)
        result = g.invoke(VALID_PAYLOAD, ctx=ctx)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["extraction_result"] is not None
        assert "TEL-DOC-20260712-042" in result["extraction_result"]
