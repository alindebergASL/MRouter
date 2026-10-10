"""Keyset pagination by id."""

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Select
from sqlalchemy.orm import InstrumentedAttribute, Session

from purser_controlplane.schemas import Page


def page[T: BaseModel](
    session: Session,
    query: Select[Any],
    id_column: InstrumentedAttribute[uuid.UUID],
    after: uuid.UUID | None,
    limit: int,
    model: type[T],
) -> Page[T]:
    if after is not None:
        query = query.where(id_column > after)
    rows = session.scalars(query.order_by(id_column).limit(limit + 1)).all()
    items = [model.model_validate(row) for row in rows[:limit]]
    next_after = rows[limit - 1].id if len(rows) > limit else None
    return Page[T](items=items, next_after=next_after)
