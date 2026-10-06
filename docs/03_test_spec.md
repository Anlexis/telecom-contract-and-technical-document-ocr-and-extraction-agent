# Test Specification — TEL-C2-002 Telecom Contract & Technical Document OCR & Extraction Agent

## 1. Test Strategy

- **Agent:** TEL-C2-002 — Telecom Contract & Technical Document OCR & Extraction
  Agent (Cat 2, DocGeneration pattern, two-layer nested graph: outer
  `AgentBaseGraph` backbone + inner `DomainWorkflowGraph` `BaseGraph`).
- **Coverage target:** ≥ 90% of `src/nodes/` + `src/graph/` branches.
- **Test types:** Unit (per node + graph wiring) · Proof-of-Boundary (framework
  security/serialization contracts) · Backbone invoke (full `Graph().invoke()`).
- **Framework provisioning:** `framework` (agenticstar-agentcore) is installed
  from the package registry. Tests import the real modules; there are no stub
  nodes and no test asserts against a stub.
- **Where a guarantee is proved:** tests that assert the TEMPLATE's own
  guarantees call `execute()` directly, with no framework wrapper in front. A
  test that drives the full graph and observes a refusal cannot tell whether
  the template refused or the platform's input gate did — and a guarantee that
  holds only while the platform gate is configured on is not one this template
  can make. End-to-end behaviour is asserted separately.
- **Both directions, always:** every screen is probed with hostile input AND
  with real text from this repository's own document corpus. A screen that
  refuses real documents is the failure that actually blocks work.
- **S-4 audit:** `emit_trace_event` is patched at the node module level in unit
  tests to avoid audit-backend calls, never via a `sys.modules` stub (which would
  break the real `shared` package the framework loads at import time).

### Test file map

| File | Scope |
|------|-------|
| `tests/unit/test_nodes.py` | All 7 domain/backbone nodes + outer & inner graph wiring |
| `tests/unit/test_caller_contract.py` | The caller-data contract at the node boundary: non-finite numerics per field, inert metadata both directions, structural bounds, control tokens, credential redaction, closed-set reasons |
| `tests/unit/test_framework_compliance_tc06_tc07.py` | TC-06/TC-07 — the framework's final security gates cannot be overridden |
| `tests/unit/test_main_node.py` | The removed `MainNode` tombstone carries no class |
| `tests/integration/test_outer_invoke_returns_domain_result.py` | Compiled outer graph end to end: domain result, redaction-is-not-extraction, credentials in a document, closed-set failure envelope, output-depends-on-input, declared config reaching the graph |
| `tests/integration/test_asgi_invoke.py` | The deployed HTTP surface: real `/invoke` with Bearer auth, the committed deployment payload posted verbatim, the refusal body, the boundary size cap, the secret namespace |
| `tests/proof_of_boundary/test_pb_invoke_order.py` | PB-6 per-node + backbone invoke order (VERIFIED_EXTERNAL) + S-1 gate + payload alignment |
| `tests/proof_of_boundary/test_import_isolation.py` | PB-4 Level-0 import isolation (AST scan) |
| `tests/proof_of_boundary/test_state_safety.py` | PB-2/PB-5 State msgpack/credential safety (AST scan) |
| `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` | PB-7 HITL interrupt-propagation (skip stub — no cross-boundary HITL) |

### Canonical valid payload (PB-6 `_VALID_PAYLOAD`)

The valid telecom-document payload used by the backbone invoke test and by
`deploy/invoke_payload.json` (the two MUST stay identical — asserted by
`test_invoke_payload_matches_pb6`):

```json
{
  "document_id": "TEL-DOC-20260712-042",
  "document_type": "service contract",
  "content": "SERVICE CONTRACT\n\nContract Number: SB-5G-2026-0042\nEffective Date: 2026-07-01\nParty: ACME TELECOM KK and BETA NETWORKS KK\nTerm: 24 months\nMonthly Fee: 12,800 yen\n\nThis agreement covers 5G NR bearer service and VoLTE for the Kanto region. The parties agree to the terms of service set out in the attached schedule.",
  "pages": 3,
  "language": "en",
  "source": "ocr_pipeline"
}
```

The party names are fictional and are deliberately chosen so the platform's
personal-data filter does NOT mask them, which keeps this fixture on the
ordinary extraction path. A second fixture with names the filter DOES mask
drives the redaction path on purpose.

Validity: `document_id` and `content` present (PreProcessNode required keys);
`document_type` "service contract" normalises to `service_contract`; the linear
extraction pipeline runs end-to-end to a structured-JSON extraction result whose
`document.document_id` and normalised `service_contract` type surface in the
final `output`.

## 2. Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Where |
|-------|------|----------------|-------|
| TC-01 | State contract: flat `TypedDict`, domain fields `NotRequired`, no Pydantic/dataclass | AST scan: 0 violations | `test_state_safety.py` |
| TC-02 | Invalid/empty/non-JSON input rejected at PreProcessNode | `status=error`, error_log populated | `TestPreProcessNode` |
| TC-03 | No JWT/credential in State | CI `gate-credential-scan`: 0 violations | CI + `test_state_safety.py` |
| TC-04 | `execute(self, state)` contract — no `_invoke_impl` | Signature `(self, state)`, `_invoke_impl` absent | `test_execute_signature_is_state_first`, `test_main_node.py` |
| TC-05 | S-4: `emit_trace_event()` called inside each node `execute()` | ≥1 domain event per node (positional form) | verified by CoE preflight S-4/#3 |
| TC-08 | S-1: `required_trust_level` enforced in `__call__` before `execute()` | ANONYMOUS caller → refused; VERIFIED_EXTERNAL → admitted | `TestS1TrustGate` |
| TC-08a | Outer `PreProcessNode` = VERIFIED_EXTERNAL; inner nodes + post_process = ANONYMOUS | trust levels asserted per node | `test_trust_level_*` |
| TC-11 | S-3 output gate on post_process | credential pattern → every output-bearing field cleared, truthy closed-set reason envelope, `status=error`; clean → pass | `TestPostProcessNode` |
| TC-12 | Output gate is the UNION of the framework detector and the local patterns | framework shapes (`sk-`, `AKIA`, JWT, connection URI) and local-only shapes (`pk-`/`ak-`, short bearer, `password = …`) all caught; telecom prose untouched | `test_output_gate_is_the_union_of_framework_and_local_patterns` |
| TC-13 | Every caller-controlled number is finite and bounded | `NaN`, `±Infinity`, `1e400`, out-of-range, bool, non-numeric → refused with a field-naming reason, on BOTH sides of the subgraph boundary | `test_pages_must_be_finite_and_in_range` |
| TC-14 | Metadata echoed into the report is inert | free text, markup, newlines, over-length → refused, value never echoed; real identifiers accepted | `test_metadata_echoed_into_the_report_must_be_inert`, `test_real_metadata_values_are_accepted` |
| TC-15 | Chat-template control tokens are screened as a class | `<\|im_start\|>`, `[INST]`, `<<SYS>>` in values, in field NAMES, `\u`-escaped, markup-spliced, nested → refused | `test_control_tokens_*` |
| TC-16 | A redacted value is not an extracted value | masked field scores 0.0, listed in `redaction.redacted_fields`, forces `needs_review` | `test_masked_value_is_not_reported_as_a_confident_extraction` |
| TC-17 | Caller-visible failures are a closed set | every refusal path returns a member of `contracts.FAILURE_REASONS`; a sentinel seeded in the request appears nowhere in the response | `TestFailureEnvelopeIsClosedSet` |
| TC-18 | Declared runtime config reaches the graph | `Graph().config` carries the declared `max_retry`/`timeout_s`; an out-of-range declared value now fails the compile | `TestDeclaredConfigReachesTheGraph` |
| TC-19 | The secret namespace matches the manifest | `server._NAMESPACE == manifest namespace == lower(industry)` | `test_the_secret_namespace_matches_the_manifest` |

## 3. Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Where |
|-------|----------|------|----------------|-------|
| PB-2 | State serialization | AST scan of `src/schemas/state.py` | primitives only; no Pydantic/dataclass | `test_state_safety.py` |
| PB-4 | Import isolation | AST scan of `src/` | 0 Level-0 (`agenticstar` / platform) imports | `test_import_isolation.py` |
| PB-5 | Checkpoint safety | no credential-named fields / prohibited types in State | inspection pass | `test_state_safety.py` |
| PB-6 | Invoke execution order (per node) | `__call__`: S-4 node_start → S-1 gate → S-2 input gate → `execute()` → S-3 output gate → S-4 node_complete | order verified for every `src/nodes/` class | `TestInvokeOrder` |
| PB-6b | Backbone invoke order | full `Graph().invoke(_VALID_PAYLOAD, ctx=VERIFIED_EXTERNAL)` | `status=success`; node_history = `[Initialize, PreProcess, DocumentExtractionGraphNode, PostProcess, Finalize]` | `TestBackboneInvokeOrder` |
| PB-6c | Real external caller | `InvocationContext(caller_trust_level=VERIFIED_EXTERNAL)` — **never** `for_internal()` | inner ANONYMOUS nodes accept the passthrough trust; SUCCESS end-to-end | `TestBackboneInvokeOrder` |
| PB-6d | Payload alignment | `deploy/invoke_payload.json["input"] == _VALID_PAYLOAD` | Stage-5 deploy-stg invoke exercises the PB-6 payload | `test_invoke_payload_matches_pb6` |
| PB-7 | HITL interrupt propagation | skip stub — `propagate_hitl=False`, no cross-boundary interrupt() checkpoint | skipped with reason (real assertion when HITL wired) | `test_pb7_hitl_interrupt_propagation.py` |

## 4. Business Logic Tests

| BL-ID | Test | Input | Expected Result | Where |
|-------|------|-------|----------------|-------|
| BL-01 | Happy-path extraction | `_VALID_PAYLOAD` | structured-JSON result; `document_id` + `service_contract` present in output | `test_backbone_invoke_succeeds_and_returns_output`, `TestInnerDomainGraph` |
| BL-02 | Document-type alias normalisation | `document_type="plan"` | normalised to `service_plan`; unknown alias → `unknown` | `test_document_type_alias_normalised`, `test_unrecognised_document_type_is_unknown` |
| BL-03 | Document-type detection from content | `document_type="unknown"` + contract keywords | `detected_type=service_contract` | `test_detects_type_when_unknown` |
| BL-04 | OCR-quality estimate + text-block segmentation | clean multi-line content | `ocr_quality ∈ [0,1]` (> 0.5 for clean); `block_count > 0` | `TestParseDocumentNode` |
| BL-05 | Field extraction (common + type-specific) | service_contract content | `contract_terms.contract_number` extracted; common dates captured | `test_extracts_common_and_type_fields` |
| BL-06 | Confidence scoring range | extracted fields + ocr_quality | `overall_confidence ∈ [0,1]`; per-field scores present | `test_scores_within_range` |
| BL-07 | Low-confidence → needs_review | short field + `ocr_quality=0.0` | `needs_review=True`; overall < threshold (0.70) | `test_low_quality_flags_needs_review` |
| BL-08 | Result assembly | extracted + confidence + document_data | `{document, extracted_fields, field_count, confidence, redaction}`; `result==extraction_result` | `TestOutputFormatNode` |
| BL-09 | Graph key coupling | inner `get_output` ↔ outer `merge_output` | 5 coupled keys mapped; `merge_output` returns changed keys only | `TestOuterGraphComposition`, `TestInnerDomainGraph` |
| BL-10 | Output depends on input | a service contract vs a network configuration sheet | different detected type, different field set, different field count | `test_document_type_and_fields_track_the_document` |
| BL-11 | Confidence tracks scan quality | clean scan vs garbled scan | lower `ocr_quality` AND lower `overall_confidence` | `test_confidence_tracks_scan_quality` |
| BL-12 | OCR quality measures the scan, not the redaction | a document the privacy filter masks | `ocr_quality` identical to the unmasked document | `test_ocr_quality_measures_the_scan_not_the_redaction` |
| BL-13 | A device password does not fail the request | network configuration sheet carrying `password = …` | `status=success`, secret absent everywhere, `redaction.credentials_removed ≥ 1`, real network fields still extracted | `test_device_password_is_redacted_and_the_request_still_succeeds` |
| BL-14 | The redaction tally crosses the subgraph boundary | credential in the document | the count reaches `document_data` and the report; a caller-supplied tally is dropped | `test_the_redaction_tally_*` |

### Negative / boundary cases

| Case | Node | Expected |
|------|------|----------|
| empty `user_input` | PreProcessNode | `status=error`, "empty" |
| invalid JSON | PreProcessNode | `status=error`, "invalid JSON" |
| JSON root not an object | PreProcessNode | `status=error`, "object" |
| missing `content` | PreProcessNode | `status=error`, "content" |
| empty `content` | PreProcessNode / InputValidateNode | `status=error`, "content" |
| empty `document_id` | InputValidateNode | `status=error`, "document_id" |
| missing `document_data` | Parse / Extract / Confidence | `status=error` |
| missing `extracted_fields` | Confidence / OutputFormat | `status=error` |
| empty `extraction_result` | PostProcessNode | `status=error` with a closed-set reason — fails CLOSED, never a placeholder answer |
| credential leak in output | PostProcessNode | every output-bearing field cleared, truthy reason envelope, `status=error` |
| non-finite `pages` | PreProcessNode / InputValidateNode | `status=error`, `invalid_numeric_field` |
| free-text `document_id` / `source` / `language` | PreProcessNode / InputValidateNode | `status=error`, `invalid_metadata_identifier`, value not echoed |
| control token anywhere in the request | PreProcessNode / InputValidateNode | `status=error`, `control_token_in_request` |
| request or document over its bound | PreProcessNode | `status=error`, `payload_too_large` / `document_content_too_large` |
| ANONYMOUS caller | framework trust gate before PreProcessNode | `status=error`, generic closed-set reason |

## 5. Test Execution Summary

- Execution: `pytest tests/` against the installed framework wheel.
- Total: 205 tests — **204 passed, 1 skipped** (PB-7 skip stub, by design).
- Gates: dep-pinning, stub-check, cat-consistency, import-isolation,
  composition, invoke-chain, credential-scan, forbidden-strings,
  manifest-schema, oss-license, audit-trace, trust-level,
  scaffold-integrity — all PASS.
- Coverage: node and graph modules exercised on success and error paths, plus
  the compiled outer graph and the real HTTP surface.

### Credential-shaped test data

The tests must drive real credential SHAPES through the input redaction and the
output gate — there is no way to prove either works without them. A literal of
that shape in a committed file is indistinguishable from a real leak to the
credential gate that guards this repository, and weakening that gate to
accommodate test data would be the wrong trade every time. The shapes are
therefore assembled from fragments at runtime in `tests/_credential_shapes.py`;
nothing in that file matches a credential pattern on its own. None of them is,
or ever was, a live credential.
