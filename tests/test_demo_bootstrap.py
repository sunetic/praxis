from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.models import models
from app.services.demo_bootstrap import bootstrap_demo_integrations


def test_demo_bootstrap_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("PRAXIS_DEMO_BOOTSTRAP", raising=False)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        assert bootstrap_demo_integrations(db) is None
        assert db.query(models.DataSource).count() == 0
        assert db.query(models.Service).count() == 0


def test_demo_bootstrap_registers_both_objects_idempotently(monkeypatch) -> None:
    monkeypatch.setenv("PRAXIS_DEMO_BOOTSTRAP", "true")
    monkeypatch.setenv("DEMO_MYSQL_APP_PASSWORD", "demo-test-password")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        first = bootstrap_demo_integrations(db)
        second = bootstrap_demo_integrations(db)

        assert first is not None
        assert first.datasource_created is True
        assert first.service_created is True
        assert second is not None
        assert second.datasource_created is False
        assert second.service_created is False
        assert db.query(models.DataSource).count() == 1
        assert db.query(models.Service).count() == 1

        datasource = db.query(models.DataSource).one()
        service = db.query(models.Service).one()
        assert datasource.name == "Demo MySQL"
        assert datasource.password == "demo-test-password"
        assert service.name == "Demo Prometheus"
        assert service.config["base_url"] == "http://prometheus-demo:9090"
        assert service.knowledge_base_ids == []
