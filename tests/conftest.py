import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Patch the cached Settings object so all tests use the test LLM key.
# Must happen before any app module imports get_settings().
from app.core.config import get_settings  # noqa: E402

_settings = get_settings()
_settings.ai_base_url = "https://example.invalid/v1"
_settings.ai_api_key = "test-api-key"
_settings.ai_model = "DeepSeek-V3.2"

# Reset the cached LLM client so it picks up the patched settings above.
import app.services.llm as _llm_module  # noqa: E402

_llm_module._llm_client = None
