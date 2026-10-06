# TEL-C2-002 — Unit test: manifest config forwarding (SR #12 Medium)
#
# DocumentExtractionGraphNode._parent_config() must load config/agent.yaml
# agent.config and forward the declared (non-None) settings to the inner
# DomainWorkflowGraph under the LangGraph ``configurable`` key — previously it
# returned {} so the manifest's declared configuration never reached the nested
# graph. v1 remains deterministic (the forwarded llm.* block is reserved for a
# future LLM-backed revision and is not consumed by the rule-based nodes), but
# the config-forwarding contract is now wired and asserted here.

from src.graph.graph import DocumentExtractionGraphNode


class TestParentConfigForwarding:
    def test_parent_config_forwards_declared_manifest_settings(self):
        cfg = DocumentExtractionGraphNode()._parent_config()
        assert "configurable" in cfg
        configurable = cfg["configurable"]
        # The manifest's declared llm.* settings are forwarded (non-None only).
        assert configurable.get("system_prompt_template") == "prompts/document_extraction.j2"
        assert configurable.get("temperature") == 0.0
        assert configurable.get("max_tokens") == 4000

    def test_parent_config_omits_absent_keys(self):
        configurable = DocumentExtractionGraphNode()._parent_config()["configurable"]
        # None-valued / absent declarations are never forwarded.
        assert all(v is not None for v in configurable.values())
        # TEL-C2-002 does not declare min_confidence_threshold (FIN-only key).
        assert "min_confidence_threshold" not in configurable
