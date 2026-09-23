"""Shared schema metadata, without constructing a database connection on import."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
