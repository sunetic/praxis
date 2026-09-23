"""Isolated SDK contracts: never load the legacy application or real credentials."""

import pytest
from pydantic_ai import models
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.agent_runs import RUNTIME_TABLES
from app.services.agent.store import RunStore


@pytest.fixture(autouse=True)
def no_network_models(monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)


@pytest.fixture
def store(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'runtime.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine, tables=RUNTIME_TABLES)
    result = RunStore(sessionmaker(engine), lease_seconds=1)
    result.create_conversation("conversation", "user")
    yield result
    engine.dispose()
