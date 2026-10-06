# Template Design Specification — TEL-C2-002 Telecom Contract & Technical Document OCR & Extraction Agent

## Position in AgentCore Architecture

- **Agent Class**: TelecomContractDocumentOCRAgent
- **L1 Base** (framework base class): AgentBaseGraph — direct framework inheritance
- **Pattern**: Cat 2 — DocGenerationAgent (two-layer nested workflow)
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible); ADR-005 JSON-serialised strings for all dict/list fields
  - Node: L1 inheritance (Template Method: `execute(self, state) -> dict` override only —
    the framework passes state alone, so no node declares a `config` parameter)
  - Graph: composition (`register_nodes()` for node substitution; Cat 2 nested via `GraphNode`)

## Domain Context

Telecom document OCR & extraction agent for Japanese telecommunications operators. Ingests the OCR/extracted text of scanned service contracts, service plans, equipment specifications, network configuration sheets, and regulatory filings, and returns structured JSON with per-field confidence scores.

**Business driver**: B2B enterprise telecom contracts are complex and paper-heavy; operators manage millions of service contracts. Digitising extraction (contract terms, service-plan details, equipment specs, network-config values, regulatory-filing data) yields immediate ROI for contract-management and legal teams.

## Architecture Overview

### Backbone (outer AgentBaseGraph — fixed 5-node pipeline)

```
START → initialize → pre_process → main(GraphNode) → post_process → finalize → END
                                         ↓ (retry, max 3)
                                       pre_process
```

### Inner Domain Workflow (DomainWorkflowGraph — linear 5-node pipeline)

```
START → input_validate → parse_document → extract_fields
          → confidence_score → output_format → END
```

### Node Configuration

| Node | Class | File | Trust | Responsibility | Input Keys | Output Keys |
|------|-------|------|-------|---------------|------------|-------------|
| initialize | InitializeNode | framework | — | session init | — | session_id, schema_version |
| pre_process | PreProcessNode | src/nodes/pre_process_node.py | VERIFIED_EXTERNAL | S-1 trust + the caller-data contract: structural bounds, control-token screen, inert metadata, finite numerics, credential redaction | user_input | validated_input, enriched_context, failure_reason |
| main | DocumentExtractionGraphNode | src/graph/graph.py | — | delegates to DomainWorkflowGraph | validated_input | extraction_result, extracted_fields, confidence_scores, overall_confidence |
| post_process | PostProcessNode | src/nodes/post_process_node.py | ANONYMOUS | S-3 output gate (framework detector ∪ local patterns); on violation clears every output-bearing field | extraction_result | formatted_output, result, failure_reason |
| finalize | FinalizeNode | framework | — | response metadata | — | response_metadata, total_time_ms |
| input_validate (inner) | InputValidateNode | src/nodes/input_validate_node.py | ANONYMOUS | domain field validation + type normalisation | validated_input | document_data |
| parse_document (inner) | ParseDocumentNode | src/nodes/parse_document_node.py | ANONYMOUS | OCR pre-process + document-type detection + OCR-quality estimate | document_data | document_data (enriched) |
| extract_fields (inner) | ExtractFieldsNode | src/nodes/extract_fields_node.py | ANONYMOUS | extract structured fields per type (rule-based; no model is invoked) | document_data | extracted_fields |
| confidence_score (inner) | ConfidenceScoreNode | src/nodes/confidence_score_node.py | ANONYMOUS | per-field + overall confidence scoring; a redacted value scores 0.0 and forces review | extracted_fields, document_data | confidence_scores, overall_confidence |
| output_format (inner) | OutputFormatNode | src/nodes/output_format_node.py | ANONYMOUS | assemble final structured-JSON result | extracted_fields, confidence_scores | extraction_result, result |

### Data Flow

```
user_input (JSON document payload)
    │
    ▼ PreProcessNode (VERIFIED_EXTERNAL, S-1)
validated_input (normalised JSON string)
enriched_context (JSON string — ADR-005)
    │
    ▼ DocumentExtractionGraphNode → DomainWorkflowGraph
    │   InputValidateNode     → document_data (JSON string — ADR-005)
    │   ParseDocumentNode      → document_data (enriched: text_blocks, detected_type, ocr_quality)
    │   ExtractFieldsNode       → extracted_fields (JSON string — ADR-005)
    │   ConfidenceScoreNode     → confidence_scores (JSON string), overall_confidence (float)
    │   OutputFormatNode        → extraction_result (str), result (str)
    ▼ merge_output
extraction_result, extracted_fields, confidence_scores, overall_confidence → outer state
    │
    ▼ PostProcessNode (ANONYMOUS, S-3)
formatted_output (S-3-gated extraction_result), result
```

### State Definition

| Field | Type | Purpose | Producer |
|-------|------|---------|----------|
| validated_input | NotRequired[Optional[str]] | Normalised document JSON string | PreProcessNode |
| enriched_context | NotRequired[Optional[str]] | JSON: {source, channel, document_id} | PreProcessNode |
| document_data | NotRequired[Optional[str]] | JSON: parsed+enriched document payload | InputValidateNode / ParseDocumentNode |
| extracted_fields | NotRequired[Optional[str]] | JSON: {document_type, fields, field_count} | ExtractFieldsNode |
| confidence_scores | NotRequired[Optional[str]] | JSON: per-field + overall confidence result | ConfidenceScoreNode |
| overall_confidence | NotRequired[Optional[float]] | Overall extraction confidence [0,1] | ConfidenceScoreNode |
| extraction_result | NotRequired[Optional[str]] | Final structured-JSON extraction document | OutputFormatNode |
| result | NotRequired[Optional[str]] | Same as extraction_result (backbone convention) | OutputFormatNode / PostProcessNode |
| failure_reason | NotRequired[Optional[str]] | Closed-set reason code — the only failure detail a caller receives | any node that refuses |
| redaction_counts | NotRequired[Optional[str]] | JSON: {credential_kind: count} removed from the document | PreProcessNode |

**ADR-005 constraint**: all dict/list-valued fields use JSON-serialised `Optional[str]`. `to_json()` / `from_json()` helpers are defined in `src/schemas/state.py` and used at every producer/consumer boundary — one contract end-to-end.

**Prohibited**: re-declaring `formatted_output` (inherited from AgentState), credentials in State, Pydantic models.

### Input Payload Schema (user_input JSON)

```json
{
  "document_id": "DOC-2026-00042",
  "document_type": "service_contract",
  "content": "raw OCR / extracted text of the scanned telecom document ...",
  "pages": 3,
  "language": "ja",
  "source": "scanned_pdf"
}
```

`document_id` and `content` are mandatory; `document_type` is auto-detected from the content
when absent or unrecognised.

Accepted values, enforced in `src/schemas/contracts.py` and re-checked on both sides of the
subgraph boundary:

| Field | Rule | Why |
|---|---|---|
| `document_id`, `source` | `^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$` | echoed verbatim into the report, so free text there would be caller-controlled output |
| `language` | BCP-47 shape, e.g. `en`, `ja`, `ja-JP` | same |
| `content` | up to 400,000 characters; NOT pattern-restricted | reading arbitrary document text is the job |
| `pages` | finite number in 0–100,000 | `NaN` and `±Infinity` parse through `float()` and then compare False against every bound, which is a silent fail-open |
| whole request | up to 1,048,576 characters, at most 32 top-level keys | a caller-supplied document is otherwise an unbounded allocation |
| any field | no chat-template control tokens (`<\|…\|>`, `[INST]`, `<<SYS>>`), screened over keys and values, raw and markup-stripped | never legitimate in a scanned document, and the report may be read by a language model |

Keys beginning with `_` are reserved for the request boundary's own channel into the inner
graph and are dropped from caller input before use.

**Credential shapes are redacted, not refused.** The framework's output gate scans every value
of every node result, and the first node's result carries the whole normalised request — so a
credential anywhere in the document would otherwise fail the run at node one with nothing the
caller could act on. A network configuration sheet carrying `password = …` is an ordinary
instance of a document type this agent accepts. The matched text is replaced with
`[REDACTED_CREDENTIAL]`, and the report states how many values were removed and of what kind.

### Output Result (structured JSON with confidence scores)

```json
{
  "document": { "document_id": "...", "document_type": "...", "ocr_quality": 0.93, "...": "..." },
  "extracted_fields": { "common": { "dates": [...], "amounts": [...] }, "contract_terms": { "...": "..." } },
  "field_count": 12,
  "confidence": { "overall_confidence": 0.86, "per_field": { "...": 0.9 }, "low_confidence_fields": [], "needs_review": false },
  "redaction": { "redacted_fields": [], "credentials_removed": 0, "credential_kinds": [] }
}
```

**A redacted value is not an extracted value.** The platform's personal-data filter masks
name-shaped and identifier-shaped text in the caller's document before any template code runs,
so `[MASKED]` arrives looking like ordinary content. Any field whose value carries a redaction
sentinel scores **0.0**, is listed in `redaction.redacted_fields` and in
`confidence.low_confidence_fields`, and forces `needs_review: true`. OCR quality is measured
with sentinels removed, so a privacy mask does not read as a poor scan.

### Failure response

A failed invocation returns `status: error` and a reason drawn from a closed set
(`contracts.FAILURE_REASONS`):

```json
{ "status": "error", "failure_reason": "invalid_numeric_field", "output": { "reason": "invalid_numeric_field" } }
```

No structured field is surfaced on a failure. `error_log` carries node-authored text and,
wherever a node interpolates a caught exception, the caller's own bytes — it is the internal
channel and is never projected to the caller. Truncating or redacting it would not make it a
closed set; only choosing from a closed set does.

## Security Configuration

| Layer | Gate | Implementation |
|-------|------|---------------|
| S-1 | Trust enforcement | PreProcessNode `required_trust_level = VERIFIED_EXTERNAL` |
| S-2 | Input validation | PreProcessNode (bounds, inert metadata, finite numerics, control tokens, credential redaction) + InputValidateNode (the same rules re-applied on the inner side of the subgraph boundary, from one shared module) |
| S-3 | Output gate | PostProcessNode `_run_output_gate()` — module-level function; the UNION of the framework's credential detector and the template's own patterns. The framework's set is the floor (a value it catches and this gate missed would make the framework raise inside the wrapper and discard the clearing); the local set adds `pk-`/`ak-` keys, short bearer tokens and `password = …` assignments, which the framework's format-based patterns do not match. On violation every output-bearing field is cleared and a truthy constant reason envelope is returned |
| S-4 | Audit logging | `emit_trace_event()` in every node's `execute()` (at least one domain-specific event) |
| S-5 | Credential handling | No credentials in State; secrets via InvocationContext only |

### Output invariants this agent does and does not enforce

**No monetary precision grid — not applicable here.** Some templates round every
monetary value in an external report to the nearest 1,000 and have the output gate
enforce that grid. This agent renders **no monetary aggregate**: it reports amount
*strings extracted verbatim from the caller's own document* (`"12,800 yen"`), computes
no total, and derives no figure of its own. A grid would rewrite the document's text
rather than round a computed value, which is the opposite of what an extraction agent
is for. The numbers it does compute — `ocr_quality`, per-field and overall confidence —
are ratios in [0, 1], rounded to three places at source.

The invariants it **does** enforce on every representation, and the mutant that proves
each is load-bearing:

| Invariant | Enforced by | Falsified by removing |
|---|---|---|
| A redaction sentinel is never reported as a confident extraction | `ConfidenceScoreNode._score_value` + `redacted_fields` | the sentinel check in the scorer |
| No credential shape reaches the report | redaction at the request boundary, then the output gate | the redaction, or the gate's clearing |
| The caller-visible failure is a constant from a closed set | `contracts.FAILURE_REASONS` + `get_output` | the `if succeeded` withholding in `get_output` |
| Every caller-controlled number is finite and in range | `contracts.finite_in_range` | the finite / range checks |
| Every caller string echoed into the report is an inert identifier | `contracts.IDENTIFIER_RE` / `LANGUAGE_RE` | the patterns |

Containment is two layers and each is separately falsifiable, which is stated here so
neither can look decorative: `PostProcessNode._contain()` clears the output-bearing
State fields, and `get_output()` withholds every structured field on a non-success
path. The node-level clearing is not redundant — the framework's own final gate scans
the dict the node returns, so leaving the blocked text in it makes the framework raise
*inside* post-process, and the wrapper's handler then returns a bare error partial that
discards the clearing entirely.

**config/agent.yaml** is the flat registry manifest — identity, entry point, trust level and the
compile-time `requires` gates. Runtime parameters live separately in **config/config.yaml** and
are passed to the graph as `Graph(config=…)`:

```yaml
max_retry: 3
timeout_s: 30
llm:
  system_prompt_template: prompts/document_extraction.j2
  temperature: 0.0
  max_tokens: 4000
security:
  s3_gate_enabled: true
```

`requires.secrets` and `requires.extras` are both empty, derived from the code: this template
calls no `ctx.secrets.require()` and constructs no platform client. Declaring a secret it does
not use would make the agent fail to compile wherever that secret is unprovisioned.

The `llm` block is **declared but inactive**: it reaches the inner graph as
`config["configurable"]`, and no node reads it. It is held for a future model-backed revision
and is named as inactive in `docs/07_operation_guide.md` so nobody tunes `temperature`
expecting an effect. `timeout_s` is likewise not read by the framework or by this template; it
describes the deployment's own request timeout.

## Framework Utilization

### Shared Components Used
- [x] InvocationContext — session_id, caller trust level
- [x] S-3: module-level `_run_output_gate()` in `post_process_node.py` — credential scan on the assembled report
- [x] S-4: `emit_trace_event()` — at least one domain-specific event per node `execute()`
- [x] `to_json()` / `from_json()` helpers in `src/schemas/state.py` — ADR-005 serialisation contract

### Composition Pattern

- **Pattern**: Cat 2 nested two-layer — GraphNode wrapping inner BaseGraph
- **Outer graph**: `TelecomContractDocumentOCRAgent(AgentBaseGraph)` — fixed 5-node backbone
- **Inner graph**: `DomainWorkflowGraph(BaseGraph)` — 5-node linear domain pipeline
- **Error propagation**: propagate (SubgraphError on inner failure; outer backbone retries pre_process)

## Import Isolation Confirmation
- [x] Template does not import the Level-0 platform SDK
- [x] Import targets: `framework/` and `shared/` only (no Level-0 SDK)

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | AgentBaseGraph | Fixed sequential extraction pipeline; no LLM reasoning loop required |
| Composition pattern | Cat 1 (flat) | Cat 2 (nested GraphNode) | Cat 2 nested | 5 sequential domain steps; DocGenerationAgent pattern |
| Document-type detection | Inline in extract | Separate ParseDocumentNode | Separate node | Single-responsibility (CoE §2-1); detection feeds field extraction |
| Confidence scoring | Inline in extract | Separate ConfidenceScoreNode | Separate node | Single-responsibility; scoring reads OCR quality + extracted values |
| State dict fields | bare dict | JSON-serialised str | JSON-serialised str | ADR-005: msgpack serialisation safety (review finding 4) |
| LLM integration | Model-backed extraction | Rule-based extraction | Rule-based | The manifest declares `generation_mode: deterministic`; the same document always produces the same result, which is what makes the confidence score meaningful |
| Credentials in a document | Refuse the request | Redact and continue | Redact | The request cannot succeed un-redacted either way, and a network configuration sheet carrying a device password is an ordinary document of a type this agent accepts |
| Caller-visible error | The internal error log | A closed set of reason codes | Closed set | The log carries node-authored text and upstream bytes; truncation is not a closed set |
