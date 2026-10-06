# Telecom Contract Document OCR Extraction Agent

AI agent for extracting structured data from scanned telecom contracts and technical documents, built with Agentic Star.

> **Category**: Cat 2 (Multi-step domain workflow)
> **Industry**: Telecommunications
> **Template ID**: TEL-C2-002

## Overview

Turns the raw text of a scanned telecommunications document into structured JSON, with a
confidence score on every field.

Feed it the OCR output of a service contract, a service-plan sheet, an equipment specification,
a network configuration sheet or a regulatory filing. It works out which of those it is,
extracts the fields that matter for that type — contract number, effective date, term, monthly
fee, VLAN, subnet, gateway, model, filing reference — alongside the dates, amounts and IP
addresses found anywhere in the text, and returns them with a per-field confidence score and a
flag for the fields a person should check. Extraction is entirely rule-based: no language model
is called, so the same document always produces the same result.

Two things it does that are easy to get wrong. Where the platform's privacy filter has masked
part of the text, or where the agent has removed a credential-shaped string from it, the
affected field is scored **0.0** and listed under `redaction.redacted_fields` — a placeholder is
never reported as a confident extraction. And every number, identifier and size in the request is
bounded and checked before any of it is processed.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode: the agent's graph is assembled from framework base classes and its security gates are
framework code, so without the platform's runtime the agent raises during graph compile or
start-up rather than starting in a partially working state. This is intentional — a half-running
agent is worse than one that refuses to start.

## Request and response

`POST /invoke` takes a JSON-encoded request object as the `input` string:

```json
{
  "input": "{\"document_id\": \"TEL-DOC-20260712-042\", \"document_type\": \"service contract\", \"content\": \"SERVICE CONTRACT\\n\\nContract Number: ...\", \"pages\": 3, \"language\": \"en\", \"source\": \"ocr_pipeline\"}"
}
```

| Field | Required | Accepted |
|---|---|---|
| `document_id` | yes | identifier, 1–64 chars from `A-Z a-z 0-9 . _ : -` |
| `content` | yes | the document text, up to 400,000 characters |
| `document_type` | no | one of the recognised aliases; anything else is detected from the text |
| `pages` | no | a finite number in 0–100,000 |
| `language` | no | a language tag such as `en`, `ja`, `ja-JP` |
| `source` | no | identifier, same shape as `document_id` |

`document_id`, `language` and `source` are echoed into the report, so they are restricted to
identifier shapes rather than free text. `content` is not restricted — reading it is the point.

A successful response carries `document`, `extracted_fields`, `field_count`, `confidence` and
`redaction`. A failed one carries a **reason code from a fixed list** and nothing else: no
exception text, no upstream response bodies, no field values from the request.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and operational documentation
```

`docs/02_design.md` describes the graph and the node contracts; `docs/03_test_spec.md` lists
the test cases.

## Customising

1. Adjust `config/config.yaml` for your own environment and policies.
2. Extend the document-type aliases and keyword signatures in `src/nodes/input_validate_node.py`
   and `src/nodes/parse_document_node.py` for the document types you handle.
3. Extend the per-type field labels in `src/nodes/extract_fields_node.py`.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
