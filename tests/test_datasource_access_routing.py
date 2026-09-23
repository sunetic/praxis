import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.models import models
from app.services.datasource.router import (
    DataSourceRoutingError,
    list_available_access_levels,
    resolve_datasource_by_access_level,
    resolve_datasource_by_role,
)


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/access-routing.db")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _datasource(
    *,
    name: str,
    db_type: str,
    cluster_key: str,
    access_level: str,
    database: str = "app",
) -> models.DataSource:
    return models.DataSource(
        name=name,
        host="127.0.0.1",
        port=3306 if db_type == "mysql" else 5432,
        db_type=db_type,
        cluster_key=cluster_key,
        access_level=access_level,
        tenant_role="user",
        user=name,
        password="test",
        database=database,
        status="active",
    )


@pytest.mark.parametrize("db_type", ["mysql", "postgresql", "postgres"])
def test_routes_admin_credential_within_same_database_cluster(db_session, db_type):
    user = _datasource(
        name="app-user",
        db_type=db_type,
        cluster_key="production-primary",
        access_level="user",
    )
    admin = _datasource(
        name="database-admin",
        db_type=db_type,
        cluster_key="production-primary",
        access_level="admin",
    )
    db_session.add_all([user, admin])
    db_session.commit()

    routed = resolve_datasource_by_access_level(db_session, user.id, "admin")

    assert routed.datasource.id == admin.id
    assert routed.requested_access_level == "admin"
    assert routed.resolved_access_level == "admin"
    assert routed.reason == "resolved_admin_by_cluster_key"
    assert list_available_access_levels(db_session, user.id) == ["user", "admin"]


def test_user_access_keeps_the_conversation_datasource(db_session):
    user = _datasource(
        name="app-user",
        db_type="mysql",
        cluster_key="local-mysql",
        access_level="user",
    )
    admin = _datasource(
        name="database-admin",
        db_type="mysql",
        cluster_key="local-mysql",
        access_level="admin",
    )
    db_session.add_all([user, admin])
    db_session.commit()

    routed = resolve_datasource_by_access_level(db_session, user.id)

    assert routed.datasource.id == user.id
    assert routed.reason == "current_datasource_matches_access_level"


def test_missing_admin_returns_structured_recoverable_error(db_session):
    user = _datasource(
        name="app-user",
        db_type="postgresql",
        cluster_key="local-postgres",
        access_level="user",
    )
    db_session.add(user)
    db_session.commit()

    with pytest.raises(DataSourceRoutingError) as exc_info:
        resolve_datasource_by_access_level(db_session, user.id, "admin")

    payload = exc_info.value.to_payload()
    assert payload["code"] == "admin_datasource_unavailable"
    assert payload["requested_access_level"] == "admin"
    assert payload["available_access_levels"] == ["user"]
    assert payload["cluster_key"] == "local-postgres"


def test_same_cluster_key_does_not_cross_database_engine_families(db_session):
    mysql_user = _datasource(
        name="mysql-user",
        db_type="mysql",
        cluster_key="shared-label",
        access_level="user",
    )
    postgres_admin = _datasource(
        name="postgres-admin",
        db_type="postgresql",
        cluster_key="shared-label",
        access_level="admin",
    )
    db_session.add_all([mysql_user, postgres_admin])
    db_session.commit()

    with pytest.raises(DataSourceRoutingError) as exc_info:
        resolve_datasource_by_access_level(db_session, mysql_user.id, "admin")

    assert exc_info.value.code == "admin_datasource_unavailable"


def test_legacy_sys_role_routes_to_admin_credential(db_session):
    user = _datasource(
        name="app-user",
        db_type="mysql",
        cluster_key="legacy-compatible",
        access_level="user",
    )
    admin = _datasource(
        name="database-admin",
        db_type="mysql",
        cluster_key="legacy-compatible",
        access_level="admin",
    )
    db_session.add_all([user, admin])
    db_session.commit()

    routed = resolve_datasource_by_role(db_session, user.id, "sys")

    assert routed.datasource.id == admin.id
    assert routed.resolved_access_level == "admin"


def test_unsupported_engine_does_not_advertise_cross_credential_routing(db_session):
    user = _datasource(
        name="oceanbase-user",
        db_type="oceanbase",
        cluster_key="future-engine",
        access_level="user",
    )
    admin = _datasource(
        name="oceanbase-admin",
        db_type="oceanbase",
        cluster_key="future-engine",
        access_level="admin",
    )
    db_session.add_all([user, admin])
    db_session.commit()

    assert list_available_access_levels(db_session, user.id) == ["user"]
    with pytest.raises(DataSourceRoutingError) as exc_info:
        resolve_datasource_by_access_level(db_session, user.id, "admin")

    assert exc_info.value.code == "admin_routing_not_supported"
