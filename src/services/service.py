"""AgentCore Platform v1.0"""

# TEL-C2-002 — Service layer (domain queries / external-API wrappers).
#
# SR #12 (Low): the previous revision shipped an unused ``Service.fetch()``
# method that raised ``NotImplementedError`` — a dead stub. TEL-C2-002 v1 is a
# self-contained, deterministic OCR/extraction pipeline: every step runs inside
# the inner DomainWorkflowGraph nodes (src/nodes/) and there is NO external
# domain service to call. The dead stub is therefore removed.
#
# This module is retained as the documented insertion point for a future
# external integration (e.g. a document-store or OCR-engine client): add a
# concrete, tested service class here and call it from the relevant inner node.
# Until then there is no service layer to implement.
#
# Rules for anything added here:
#   - domain queries / external-API wrappers ONLY — no business logic, routing,
#     or credentials (secrets come from framework.secrets, never hardcoded)
#   - nodes call this; this calls shared/services/ for external integrations
