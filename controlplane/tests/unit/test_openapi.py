"""A1: the OpenAPI document is stable, complete, and matches the committed copy."""

import json
from pathlib import Path

from purser_controlplane.api.deps import PUBLIC_PATHS
from purser_controlplane.app import api_routes, build_app
from purser_controlplane.openapi import document, render

COMMITTED = Path(__file__).resolve().parents[2] / "openapi" / "admin-api.json"


def test_openapi_3_1_and_deterministic() -> None:
    assert document()["openapi"] == "3.1.0"
    assert render() == render()


def test_committed_document_matches_fastapi() -> None:
    assert COMMITTED.read_text(encoding="utf-8") == render(), "run make cp-openapi"


def test_every_route_is_documented_with_a_stable_operation_id() -> None:
    paths = document()["paths"]
    operation_ids = []
    for route in api_routes(build_app()):
        for method in route.methods:
            operation = paths[route.path][method.lower()]
            operation_ids.append(operation["operationId"])
            if route.path not in PUBLIC_PATHS:
                assert operation["security"] == [{"HTTPBearer": []}], route.path
    assert len(operation_ids) == len(set(operation_ids))
    # Explicit camelCase IDs, not FastAPI's generated function-path names.
    assert all("_" not in op for op in operation_ids)


def test_document_carries_no_server_or_secret() -> None:
    text = json.dumps(document())
    assert "servers" not in document()
    assert "dev-only-not-a-secret" not in text
