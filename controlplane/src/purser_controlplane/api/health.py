"""Liveness and readiness. Public; they answer with a status word only."""

import logging

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from purser_controlplane.auth.oidc import AuthUnavailableError, OIDCVerifier
from purser_controlplane.db import Database
from purser_controlplane.schemas import Status

log = logging.getLogger("purser.health")
router = APIRouter(tags=["health"])


@router.get("/healthz", operation_id="healthz", response_model=Status)
def healthz() -> Status:
    """The process is up."""
    return Status(status="ok")


@router.get(
    "/readyz",
    operation_id="readyz",
    response_model=Status,
    responses={503: {"model": Status, "description": "Not ready"}},
)
def readyz(request: Request, response: Response) -> Status:
    """The database answers and the auth service's keys are loaded."""
    databases: tuple[Database, ...] = (request.app.state.db, request.app.state.operator_db)
    verifier: OIDCVerifier = request.app.state.verifier
    try:
        for database in databases:
            with database.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        if not verifier.ready:
            verifier.load()
    except (AuthUnavailableError, Exception):
        log.warning("not ready")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Status(status="not_ready")
    return Status(status="ready")
