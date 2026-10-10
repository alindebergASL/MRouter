"""Database engines and the per-transaction row-level-security context.

The API connects as roles without BYPASSRLS: the request path's role for
member routes, and a separate operator role for operator routes. Every
transaction starts by setting the caller's context with set_config(name,
value, true), which is SET LOCAL with bind parameters: it lasts until the
transaction ends and can't leak to the next request on a pooled connection.
The context lives in session.info and is re-applied at the start of every
transaction, so a commit in the middle of a request doesn't drop it.

    purser.org_id        the org whose rows this transaction may see (A5)
    purser.operator_sub  an operator's subject; grants the operator role's
                         policies only if it names a row in platform_operators.
                         No policy for the request path's role reads it.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

from sqlalchemy import Connection, CursorResult, Engine, Executable, create_engine, event, text
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

ORG_KEY = "purser.org_id"
OPERATOR_KEY = "purser.operator_sub"
_CONTEXT_KEYS = (ORG_KEY, OPERATOR_KEY)
_SET = text("SELECT set_config(:name, :value, true)")


def _apply_context(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    for key in _CONTEXT_KEYS:
        value = session.info.get(key)
        if value is not None:
            connection.execute(_SET, {"name": key, "value": value})


def _reset_on_checkin(dbapi_connection: Any, connection_record: Any) -> None:
    # Belt and braces: transaction-local settings are already gone after the
    # pool's rollback, but clear any session-level setting too.
    try:
        with dbapi_connection.cursor() as cursor:
            cursor.execute("RESET ALL")
        dbapi_connection.commit()
    except Exception:
        connection_record.invalidate()


class Database:
    def __init__(self, url: str, *, pool_size: int = 5) -> None:
        # hide_parameters: bound values (emails, names) never reach logs or errors.
        self.engine: Engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=pool_size,
            hide_parameters=True,
        )
        event.listen(self.engine, "checkin", _reset_on_checkin)
        self.sessions: sessionmaker[Session] = sessionmaker(self.engine, expire_on_commit=False)
        event.listen(self.sessions, "after_begin", _apply_context)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.sessions()
        try:
            yield session
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()


def affected(session: Session, statement: Executable) -> int:
    """Run an UPDATE or DELETE and return how many rows it changed."""
    return cast("CursorResult[Any]", session.execute(statement)).rowcount


def set_context(
    session: Session, *, org_id: uuid.UUID | None = None, operator_sub: uuid.UUID | None = None
) -> None:
    """Scope this session's transactions to one org, or to an operator."""
    values = {ORG_KEY: org_id, OPERATOR_KEY: operator_sub}
    for key, value in values.items():
        if value is None:
            continue
        session.info[key] = str(value)
        if session.in_transaction():
            session.execute(_SET, {"name": key, "value": str(value)})
