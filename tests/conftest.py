import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Patch the cached Settings object so tests never rely on committed credentials.
# Must happen before any app module imports get_settings().
from app.core.config import get_settings  # noqa: E402

_settings = get_settings()
_settings.ai_base_url = os.getenv("TEST_AI_BASE_URL", "https://example.invalid/v1")
_settings.ai_api_key = os.getenv("TEST_AI_API_KEY", "test-api-key")
_settings.ai_model = os.getenv("TEST_AI_MODEL", "test-model")


@pytest.fixture(autouse=True)
def no_live_model_requests(monkeypatch):
    """Unit/API tests must opt out of live models; real trials use tools/*_smoke.py."""
    from pydantic_ai import models

    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
