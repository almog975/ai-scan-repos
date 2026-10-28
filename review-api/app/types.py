"""Portable column types: native uuid/jsonb on Postgres, text/JSON on sqlite (tests/dev)."""
import uuid

from sqlalchemy import CHAR, JSON
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import TypeDecorator


class GUID(TypeDecorator):
    impl = CHAR(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.UUID(as_uuid=False))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        return None if value is None else str(uuid.UUID(str(value)))

    def process_result_value(self, value, dialect):
        return None if value is None else str(value)


JSONType = JSON().with_variant(postgresql.JSONB(), "postgresql")


def new_uuid() -> str:
    return str(uuid.uuid4())
