# TEL-C2-002 — the deployed HTTP surface, driven end to end.
#
# The compiled graph working is not the same claim as the DEPLOYED AGENT being
# able to serve a request. These drive the real ASGI application through
# POST /invoke, authenticated exactly as the deployment does, and assert the
# caller receives real extracted values rather than a baseline.
#
# They also pin the two adapter facts that are invisible from inside the graph:
# the secret namespace must match the manifest, and the request body is bounded
# at the boundary as well as inside the agent.

import importlib
import json
import os

import pytest

from src.schemas import contracts
from tests import _credential_shapes as shapes

_TOKEN = "deploy-token-abc"

_VALID_INPUT = json.dumps(
    {
        "document_id": "TEL-DOC-20260712-042",
        "document_type": "service contract",
        "content": (
            "SERVICE CONTRACT\n\n"
            "Contract Number: SB-5G-2026-0042\n"
            "Effective Date: 2026-07-01\n"
            "Party: ACME TELECOM KK and BETA NETWORKS KK\n"
            "Term: 24 months\n"
            "Monthly Fee: 12,800 yen\n\n"
            "This agreement covers 5G NR bearer service and VoLTE for the Kanto region. "
            "The parties agree to the terms of service set out in the attached schedule."
        ),
        "pages": 3,
        "language": "en",
        "source": "ocr_pipeline",
    }
)


@pytest.fixture()
def client(monkeypatch):
    testclient = pytest.importorskip("starlette.testclient")
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", _TOKEN)
    import src.api.server as srv

    srv = importlib.reload(srv)
    yield testclient.TestClient(srv.app), srv


def _post(client, payload_input, token=_TOKEN):
    tc, _ = client
    headers = {"authorization": f"Bearer {token}"} if token else {}
    return tc.post("/invoke", json={"input": payload_input, "session_id": "s1"}, headers=headers)


def test_health_reports_the_agent_name(client):
    tc, _ = client
    body = tc.get("/health").json()
    assert body["status"] == "ok"
    assert body["agent"] == "TelecomContractDocumentOCRAgent"


def test_authenticated_invoke_returns_real_extracted_values(client):
    """The deployed agent serves a request and the answer depends on the document."""
    r = _post(client, _VALID_INPUT)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success", body

    report = json.loads(body["formatted_output"])
    assert report["document"]["document_id"] == "TEL-DOC-20260712-042"
    assert report["document"]["document_type"] == "service_contract"
    assert report["field_count"] > 0
    terms = report["extracted_fields"]["contract_terms"]
    assert terms["contract_number"] == "SB-5G-2026-0042"
    assert terms["term"] == "24 months"
    assert report["confidence"]["overall_confidence"] > 0.0
    assert report["redaction"]["redacted_fields"] == []


def test_the_committed_deployment_payload_is_the_tested_payload(client):
    """deploy/invoke_payload.json is posted verbatim by the deployment check.

    A payload that does not satisfy the entry contract produces a well-formed
    200 whose BODY reports status=error, which every surrounding assertion
    passes. Posting the committed file through the real app is the only check
    that catches it.
    """
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[2]
    committed = json.loads((repo / "deploy" / "invoke_payload.json").read_text())
    assert committed["input"] == _VALID_INPUT, "the deployment payload and the tested payload have drifted apart"
    r = _post(client, committed["input"])
    assert r.status_code == 200
    assert r.json()["status"] == "success", r.json()


def test_unauthenticated_and_wrong_token_are_refused(client):
    assert _post(client, _VALID_INPUT, token=None).status_code == 401
    assert _post(client, _VALID_INPUT, token="nope").status_code == 401


def test_refusal_body_carries_only_a_reason_code(client):
    r = _post(client, "this is not json at all")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert body["failure_reason"] in contracts.FAILURE_REASONS
    assert body["output"] == {"reason": body["failure_reason"]}
    assert body["formatted_output"] is None
    assert "Traceback" not in json.dumps(body)


def test_a_credential_in_the_document_does_not_break_the_request(client):
    """This returned an empty error before the redaction was added.

    The framework's output gate scans every value of every node result, so a
    credential-shaped string anywhere in the document made the first node fail
    with a traceback. The secret must be gone AND the request must succeed.
    """
    secret = shapes.password_assignment()
    payload = json.loads(_VALID_INPUT)
    payload["content"] = payload["content"] + "\nConsole " + secret + "\n"
    r = _post(client, json.dumps(payload))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success", body
    assert secret not in r.text
    assert "supersecret123" not in r.text
    assert json.loads(body["formatted_output"])["redaction"]["credentials_removed"] >= 1


def test_the_request_body_is_bounded_at_the_boundary(client):
    tc, _ = client
    oversize = "x" * (contracts.MAX_PAYLOAD_CHARS + 1)
    r = tc.post(
        "/invoke",
        json={"input": oversize, "session_id": "s"},
        headers={"authorization": f"Bearer {_TOKEN}"},
    )
    assert r.status_code == 422  # rejected by the request model, before the agent


def test_the_secret_namespace_matches_the_manifest(client):
    """A namespace that does not match the manifest resolves to a different
    store, so every secret lookup misses at deploy time rather than failing at
    review time."""
    import pathlib
    import re

    _, srv = client
    repo = pathlib.Path(__file__).resolve().parents[2]
    manifest = (repo / "config" / "agent.yaml").read_text()
    declared = re.search(r'^namespace:\s*"?([\w.\-]+)"?', manifest, re.M).group(1)
    name = re.search(r'^name:\s*"?([\w.\-]+)"?', manifest, re.M).group(1)
    industry = re.search(r'^industry:\s*"?([\w.\-]+)"?', manifest, re.M).group(1)

    assert declared == industry.lower()
    assert srv._NAMESPACE == declared
    assert srv._AGENT_NAME == name
    assert srv._ENTRYPOINT_SECRETS._namespace == declared
    assert srv.agent._secrets_provider._namespace == declared


def test_no_token_configured_allows_anonymous_but_the_agent_still_refuses(monkeypatch):
    """With no token set the adapter does not 401 — and the agent's own trust
    requirement is then what refuses the caller, not the adapter."""
    testclient = pytest.importorskip("starlette.testclient")
    monkeypatch.delenv("INVOKE_AUTH_TOKEN", raising=False)
    assert "INVOKE_AUTH_TOKEN" not in os.environ
    import src.api.server as srv

    srv = importlib.reload(srv)
    tc = testclient.TestClient(srv.app)
    r = tc.post("/invoke", json={"input": _VALID_INPUT, "session_id": "s"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert body["failure_reason"] in contracts.FAILURE_REASONS
