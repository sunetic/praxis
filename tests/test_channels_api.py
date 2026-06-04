from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import channels as channels_api
from app.db.database import Base
from app.models import models
from app.services.platform.object_tools import ObjectToolService


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def session_factory(tmp_path: Path):
    db_path = tmp_path / "channels-api.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


class _FakeChannelDelivery:
    async def send(self, *, channel: models.Channel, payload: dict[str, Any] | None = None):
        return {
            "provider": channel.provider,
            "channel_id": channel.id,
            "ok": True,
            "payload": payload or {},
        }


@pytest.mark.anyio
async def test_channels_api_crud_and_send(monkeypatch: pytest.MonkeyPatch, session_factory):
    monkeypatch.setattr(
        channels_api,
        "_object_service",
        ObjectToolService(
            session_factory=session_factory,
            channel_delivery=_FakeChannelDelivery(),
        ),
    )

    created = await channels_api.create_channel(
        {
            "name": "钉钉通知",
            "provider": "dingtalk",
            "config": {
                "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=test-token",
                "security": {"mode": "keyword", "keyword": "报警"},
                "template": {"type": "text", "body": "报警测试"},
            },
        }
    )
    channel_id = int(created["id"])

    listed = await channels_api.list_channels(provider=None, status_text=None)
    assert len(listed) == 1
    assert listed[0]["id"] == channel_id

    updated = await channels_api.update_channel(
        channel_id,
        {
            "template": {"type": "markdown", "title": "告警", "body": "### 告警\n报警测试"},
        },
    )
    assert updated["id"] == channel_id

    sent = await channels_api.send_channel_message(
        channel_id,
        {"content": "报警: cpu > 90%"},
    )
    assert sent["object_type"] == "channel"
    assert sent["action"] == "send"
    assert sent["result"]["ok"] is True

    await channels_api.delete_channel(channel_id)
    after_delete = await channels_api.list_channels()
    assert after_delete == []
