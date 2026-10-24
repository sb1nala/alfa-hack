"""Exercise wire behavior and the checked-in contract without a database."""

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate as validate_openapi
from pydantic import TypeAdapter, ValidationError

from app.api import examples as ex
from app.api.events import EVENT_EXAMPLES, STREAM_EXAMPLES
from app.config import Settings
from app.dependencies import (
    accessible_application,
    application_document,
    database_session,
    owned_application,
    require_csrf,
    require_role,
)
from app.export_openapi import DEFAULT_OUTPUT, render_openapi
from app.main import create_app
from app.schemas.applications import ClientApplication, SSEEvent
from app.schemas.common import ApplicationStatus, ErrorResponse
from app.schemas.decisions import PublicDecision
from app.schemas.underwriting import ManualDecisionRequest, UnderwriterApplication

SCHEMA = create_app().openapi()
METHODS = {"get", "post", "patch", "put", "delete"}
OPERATIONS = [
    (path, method, operation)
    for path, item in SCHEMA["paths"].items()
    if path.startswith("/api/")
    for method, operation in item.items()
    if method in METHODS
]


def validate(value, schema):
    # Keep local component refs anchored to the full document, also for subschemas.
    Draft202012Validator(
        {"components": SCHEMA["components"], **schema},
        format_checker=FormatChecker(),
    ).validate(value)


def resolve(schema):
    if "$ref" in schema:
        return SCHEMA["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]]
    return schema


def wire_request(path, method, operation):
    path = path.replace("{application_id}", ex.APPLICATION_ID).replace(
        "{document_id}", ex.DOCUMENT_ID
    )
    kwargs = {"params": {}, "headers": {}}
    for param in operation.get("parameters", []):
        if param["in"] == "path":
            continue
        # Exercise documented parameter examples and required parameters.
        schema = param["schema"]
        values = schema.get("examples", [])
        if values:
            value = values[0]
        elif "default" in schema:
            value = schema["default"]
        elif param.get("required"):
            raise AssertionError(f"Required parameter without example: {param}")
        else:
            continue
        if value is not None:
            target = "params" if param["in"] == "query" else "headers"
            kwargs[target][param["name"]] = value if isinstance(value, list) else str(value)
    content = operation.get("requestBody", {}).get("content", {})
    for media_type, media in content.items():
        values = media["examples"]
        body = deepcopy(next(iter(values.values()))["value"])
        if media_type == "application/json":
            kwargs["json"] = body
        elif media_type == "multipart/form-data":
            file = body.pop("file")
            kwargs["files"] = {"file": ("demo.txt", file.encode(), "text/plain")}
            kwargs["data"] = body
        else:
            raise AssertionError(media_type)
    return method.upper(), path, kwargs


@pytest.mark.parametrize(
    "path,method,operation", OPERATIONS, ids=[f"{m} {p}" for p, m, _ in OPERATIONS]
)
def test_operations_require_authentication_before_business_stubs(path, method, operation):
    if not operation["x-required-roles"]:
        return  # Anonymous auth is exercised against PostgreSQL in test_auth_postgres.py.
    settings = Settings(
        _env_file=None,
        postgres_password="test-only",
        auth_jwt_secret="test-only-secret-of-at-least-32-characters",
    )
    with TestClient(create_app(settings)) as client:
        verb, url, kwargs = wire_request(path, method, operation)
        response = client.request(verb, url, **kwargs)
        assert response.status_code == 401
        error = ErrorResponse.model_validate(response.json())
        assert error.code == "UNAUTHENTICATED"
        assert error.field_errors == []
        assert str(error.request_id) == response.headers["X-Request-ID"]
        validate(response.json(), {"$ref": "#/components/schemas/ErrorResponse"})
        assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    "path,method,operation", OPERATIONS, ids=[f"{m} {p}" for p, m, _ in OPERATIONS]
)
def test_contract_structure_and_examples_per_operation(path, method, operation):
    implemented = (
        path.startswith("/api/auth/")
        or path in {"/api/products", "/api/companies/lookup", "/api/applications"}
        or (path == "/api/applications/{application_id}" and method in {"get", "patch"})
        or path
        in {
            "/api/applications/{application_id}/submit",
            "/api/applications/{application_id}/cancel",
            "/api/applications/{application_id}/accept-offer",
            "/api/applications/{application_id}/additional-info",
            "/api/applications/{application_id}/sign",
            "/api/applications/{application_id}/documents",
            "/api/applications/{application_id}/documents/{document_id}/download",
            "/api/underwriter/applications",
            "/api/underwriter/applications/{application_id}",
            "/api/underwriter/applications/{application_id}/decision",
            "/api/applications/{application_id}/history",
            "/api/applications/{application_id}/events",
            "/api/underwriter/stats",
        }
    )
    assert ("501" in operation["responses"]) == (not implemented)
    assert "422" in operation["responses"]
    assert any(int(code) < 300 for code in operation["responses"])
    assert operation["operationId"]
    for response in operation["responses"].values():
        for media in response.get("content", {}).values():
            assert "examples" in media, (path, method)
            for example in media["examples"].values():
                validate(example["value"], media["schema"])
    for media in operation.get("requestBody", {}).get("content", {}).values():
        assert media["examples"]
        for example in media["examples"].values():
            validate(example["value"], media["schema"])
    for param in operation.get("parameters", []):
        for value in param["schema"].get("examples", []):
            validate(value, param["schema"])
    roles = operation["x-required-roles"]
    expected_security = {}
    if roles:
        expected_security["SessionCookie"] = []
        assert "401" in operation["responses"]
        assert "403" in operation["responses"]
    if method in {"post", "patch", "put", "delete"}:
        expected_security["CSRFToken"] = []
    assert operation["security"] == ([expected_security] if expected_security else [])


def test_openapi_and_all_refs_are_valid():
    validate_openapi(SCHEMA)
    assert SCHEMA["openapi"].startswith("3.1.")
    ids = [op["operationId"] for _, _, op in OPERATIONS]
    assert len(set(ids)) == len(ids)
    assert len(OPERATIONS) == 24

    def visit(node):
        if isinstance(node, dict):
            if "$ref" in node:
                assert node["$ref"].startswith("#/components/schemas/")
                resolve(node)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(SCHEMA)


INVALID = [
    (
        "POST",
        "/api/auth/login",
        {"json": {"login": "", "password": "secret-do-not-echo", "extra": "secret-do-not-echo"}},
    ),
    ("POST", "/api/applications", {"json": {"amount": 100000, "term_months": 1.5}}),
    ("POST", "/api/applications", {"json": {"amount": "0.00"}}),
    ("POST", "/api/applications", {"json": {"amount": "1e5"}}),
    ("POST", "/api/applications", {"json": {"annual_revenue": "-1.00"}}),
    ("POST", "/api/applications", {"json": {"term_months": True}}),
    ("PATCH", f"/api/applications/{ex.APPLICATION_ID}", {"json": {"expected_revision": "1"}}),
    ("PATCH", f"/api/applications/{ex.APPLICATION_ID}", {"json": {"expected_revision": 0}}),
    (
        "POST",
        f"/api/applications/{ex.APPLICATION_ID}/submit",
        {"json": {**ex.REVISION, "consent_to_checks": True}},
    ),
    (
        "POST",
        f"/api/applications/{ex.APPLICATION_ID}/submit",
        {
            "json": {**ex.REVISION, "consent_to_checks": False},
            "headers": {"Idempotency-Key": ex.EVENT_ID},
        },
    ),
    ("POST", f"/api/applications/{ex.APPLICATION_ID}/accept-offer", {"json": ex.REVISION}),
    (
        "POST",
        f"/api/applications/{ex.APPLICATION_ID}/additional-info",
        {"json": {**ex.REVISION, "comment": ""}},
    ),
    (
        "POST",
        f"/api/applications/{ex.APPLICATION_ID}/sign",
        {"json": {**ex.REVISION, "offer_id": ex.OFFER_ID, "consent": False}},
    ),
    ("GET", "/api/applications/not-uuid", {}),
    ("GET", "/api/companies/lookup", {"params": {"inn": "wrong"}}),
    ("GET", "/api/applications", {"params": {"limit": 0}}),
    ("GET", "/api/applications", {"params": {"status": "UNKNOWN"}}),
    (
        "GET",
        f"/api/applications/{ex.APPLICATION_ID}/events",
        {"headers": {"Last-Event-ID": "invalid"}},
    ),
    ("GET", f"/api/applications/{ex.APPLICATION_ID}/history", {"params": {"cursor": "invalid"}}),
    (
        "POST",
        f"/api/applications/{ex.APPLICATION_ID}/documents",
        {"data": {"expected_revision": 1, "document_type": "demo"}},
    ),
    (
        "POST",
        f"/api/applications/{ex.APPLICATION_ID}/documents",
        {
            "files": {"file": ("demo.txt", b"demo")},
            "data": {"expected_revision": 0, "document_type": "demo"},
        },
    ),
    (
        "POST",
        f"/api/underwriter/applications/{ex.APPLICATION_ID}/decision",
        {"json": {**ex.MANUAL_REQUESTS["rejected"], "outcome": "MANUAL_REVIEW"}},
    ),
    (
        "POST",
        "/api/auth/login",
        {
            "content": '{"password":"secret-do-not-echo",',
            "headers": {"Content-Type": "application/json"},
        },
    ),
]


@pytest.mark.parametrize("method,url,kwargs", INVALID)
def test_invalid_requests_use_documented_error(method, url, kwargs):
    app = create_app()

    # Isolate request/schema validation. The real guards are tested with PostgreSQL.
    async def allow():
        return None

    for roles in (("client",), ("underwriter",), ("client", "underwriter")):
        app.dependency_overrides[require_role(*roles)] = allow
    app.dependency_overrides[database_session] = allow
    for guard in (require_csrf, owned_application, accessible_application, application_document):
        app.dependency_overrides[guard] = allow
    response = TestClient(app).request(method, url, **kwargs)
    assert response.status_code == 422, response.text
    error = ErrorResponse.model_validate(response.json())
    assert error.code == "VALIDATION_ERROR"
    assert error.field_errors
    assert str(error.request_id) == response.headers["X-Request-ID"]
    assert "secret-do-not-echo" not in response.text
    validate(response.json(), {"$ref": "#/components/schemas/ErrorResponse"})


def test_five_outcomes_and_four_manual_transitions():
    adapter = TypeAdapter(PublicDecision)
    for decision in ex.DECISIONS.values():
        adapter.validate_python(decision)
    manual = TypeAdapter(ManualDecisionRequest)
    for body in ex.MANUAL_REQUESTS.values():
        manual.validate_python(body)
    with pytest.raises(ValidationError):
        manual.validate_python({**ex.MANUAL_REQUESTS["rejected"], "outcome": "MANUAL_REVIEW"})
    with pytest.raises(ValidationError):
        adapter.validate_python({**ex.DECISIONS["needs_info"], "required_documents": []})
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {key: val for key, val in ex.DECISIONS["approved"].items() if key != "offer"}
        )


def test_client_schemas_cannot_reach_bank_fields():
    forbidden = {
        "scoring_factors",
        "analytical_explanation",
        "contribution",
        "policy_version",
        "review_reason",
        "risk_result",
    }

    def walk(node, seen=None):
        seen = set() if seen is None else seen
        if isinstance(node, dict):
            if "$ref" in node:
                if node["$ref"] not in seen:
                    seen.add(node["$ref"])
                    walk(resolve(node), seen)
            assert not forbidden.intersection(node.get("properties", {}))
            for value in node.values():
                walk(value, seen)
        elif isinstance(node, list):
            for value in node:
                walk(value, seen)

    for path, _, operation in OPERATIONS:
        if "/underwriter/" not in path:
            walk(operation)
    for application in ex.APPLICATION_EXAMPLES.values():
        ClientApplication.model_validate(application)
    UnderwriterApplication.model_validate(ex.BANK_APPLICATION)
    leaked = deepcopy(ex.APPLICATION_EXAMPLES["approved"])
    leaked["decision"]["scoring_factors"] = []
    with pytest.raises(ValidationError):
        ClientApplication.model_validate(leaked)


def test_sse_frames_and_schema():
    media = SCHEMA["paths"]["/api/applications/{application_id}/events"]["get"]["responses"]["200"][
        "content"
    ]
    assert set(media) == {"text/event-stream"}
    for name, frame in EVENT_EXAMPLES.items():
        SSEEvent.model_validate(frame)
        validate(frame, media["text/event-stream"]["x-event-schema"])
        raw = STREAM_EXAMPLES[name]
        assert raw.endswith("\n\n")
        lines = dict(line.split(": ", 1) for line in raw.strip().splitlines())
        assert UUID(lines["id"])
        assert lines["event"] == frame["event"]
        assert json.loads(lines["data"]) == frame["data"]
        assert frame["event"] == frame["data"]["event_type"]
    assert STREAM_EXAMPLES["heartbeat"] == ": heartbeat\n\n"


def test_statuses_match_team_document():
    states = Path(__file__).resolve().parents[2] / "docs" / "application-states.md"
    import re

    table_states = set(re.findall(r"^\| `([A-Z_]+)` \|", states.read_text(), re.MULTILINE))
    assert table_states == {status.value for status in ApplicationStatus}


def test_export_without_secrets_or_database_is_repeatable(tmp_path):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POSTGRES_", "DB_", "AUTH_"))
    }
    output = tmp_path / "openapi.yaml"
    command = [sys.executable, "-m", "app.export_openapi", "--output", str(output)]
    cwd = Path(__file__).resolve().parents[1]
    subprocess.run(command, cwd=cwd, env=env, check=True, capture_output=True)
    first = output.read_bytes()
    subprocess.run(command, cwd=cwd, env=env, check=True, capture_output=True)
    assert first == output.read_bytes() == DEFAULT_OUTPUT.read_bytes()
    assert yaml.safe_load(first) == SCHEMA
    assert TestClient(create_app()).get("/openapi.json").json() == SCHEMA
    assert render_openapi().encode() == first
    subprocess.run([*command, "--check"], cwd=cwd, env=env, check=True, capture_output=True)
    output.write_text("outdated: true\n")
    assert (
        subprocess.run([*command, "--check"], cwd=cwd, env=env, capture_output=True).returncode == 1
    )
