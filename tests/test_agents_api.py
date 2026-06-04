from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import agents as agents_api
from app.db.database import Base
from app.models import models
from app.schemas import schemas


def _build_session(tmp_path, filename: str):
    db_path = tmp_path / filename
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    return engine, Session()


def test_run_agent_creates_conversation_with_selected_datasource_and_skills(tmp_path):
    engine, db = _build_session(tmp_path, "agents-run.db")
    try:
        ds1 = models.DataSource(
            name="user-a",
            host="127.0.0.1",
            port=2881,
            db_type="oceanbase",
            cluster_key="cluster-a",
            tenant_role="user",
            user="u1",
            database="db1",
            status="active",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        ds2 = models.DataSource(
            name="sys-a",
            host="127.0.0.1",
            port=2881,
            db_type="oceanbase",
            cluster_key="cluster-a",
            tenant_role="sys",
            user="u2",
            database="db2",
            status="active",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        agent = models.Agent(
            name="ops-agent",
            prompt="you are ops",
            tools=["execute_sql"],
            skills=["skill-a", "skill-b"],
            agent_type="custom",
            status="active",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add_all([ds1, ds2, agent])
        db.commit()

        result = agents_api.run_agent(
            agent.id,
            schemas.AgentRunRequest(datasource_ids=[ds2.id, ds1.id]),
            db=db,
        )

        assert result.datasource_ids == [ds2.id, ds1.id]
        assert result.conversation.agent_id == agent.id
        assert result.conversation.datasource_id == ds2.id
        assert result.conversation.active_skills == ["skill-a", "skill-b"]
    finally:
        db.close()
        engine.dispose()


def test_run_agent_rejects_inactive_datasource(tmp_path):
    engine, db = _build_session(tmp_path, "agents-run-inactive.db")
    try:
        inactive_ds = models.DataSource(
            name="ds-off",
            host="127.0.0.1",
            port=2881,
            db_type="oceanbase",
            cluster_key="cluster-a",
            tenant_role="user",
            user="u1",
            database="db1",
            status="inactive",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        agent = models.Agent(
            name="ops-agent",
            prompt="you are ops",
            tools=["execute_sql"],
            skills=[],
            agent_type="custom",
            status="active",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add_all([inactive_ds, agent])
        db.commit()

        with pytest.raises(HTTPException) as excinfo:
            agents_api.run_agent(
                agent.id,
                schemas.AgentRunRequest(datasource_ids=[inactive_ds.id]),
                db=db,
            )

        assert excinfo.value.status_code == 400
        assert "Datasource not active" in str(excinfo.value.detail)
    finally:
        db.close()
        engine.dispose()
