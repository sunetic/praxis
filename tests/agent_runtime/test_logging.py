import io
import logging

from app.core.logging import configure_logging


def test_app_debug_does_not_enable_private_model_transport_dumps(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr("sys.stderr", output)
    # Avoid writing test secrets into a real application log.
    monkeypatch.setattr(
        "logging.handlers.RotatingFileHandler", lambda *a, **kw: logging.NullHandler()
    )
    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    names = ("openai", "httpx", "httpx2", "httpcore", "httpcore2")
    levels = {name: logging.getLogger(name).level for name in names}
    try:
        configure_logging(debug=True)
        for name in names:
            logging.getLogger(f"{name}.transport").debug(
                "Authorization: TEST_SECRET Cookie: TEST_COOKIE"
            )
        logging.getLogger("app.agent").debug("run=example status=finished")
        assert "TEST_SECRET" not in output.getvalue()
        assert "TEST_COOKIE" not in output.getvalue()
        assert "run=example status=finished" in output.getvalue()
    finally:
        for handler in root.handlers[:]:
            if handler not in old_handlers:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(old_level)
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)
