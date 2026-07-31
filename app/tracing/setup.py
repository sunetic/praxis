"""OpenTelemetry initialization and auto-instrumentation setup."""

from app.core.logging import get_logger

logger = get_logger("app.tracing")

_provider = None


def init_tracing(settings, app=None) -> None:
    """Initialize OpenTelemetry with auto-instrumentors and SQLite exporter."""
    global _provider

    if not settings.tracing_enabled:
        logger.info("tracing_disabled")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

        from app.tracing.exporter import SQLiteSpanExporter
    except ImportError as e:
        logger.warning("tracing_init_skip_missing_dep error=%s", e)
        return

    resource = Resource.create({"service.name": settings.app_name})
    sampler = TraceIdRatioBased(settings.tracing_sample_rate)
    provider = TracerProvider(resource=resource, sampler=sampler)

    exporter = SQLiteSpanExporter(db_path=settings.tracing_db_path)
    provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    _provider = provider

    _instrument_fastapi(app)
    _instrument_aiohttp()
    _instrument_httpx()
    _instrument_sqlalchemy(settings)

    logger.info(
        "tracing_init_ok sample_rate=%s db_path=%s",
        settings.tracing_sample_rate,
        settings.tracing_db_path,
    )


def shutdown_tracing() -> None:
    """Flush and shut down the tracer provider."""
    global _provider
    if _provider is not None:
        _provider.shutdown()
        _provider = None
        logger.info("tracing_shutdown_ok")


def _instrument_fastapi(app=None) -> None:
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        if app is not None:
            FastAPIInstrumentor.instrument_app(app)
        else:
            FastAPIInstrumentor.instrument()
        logger.info("tracing_instrumentor_ok name=fastapi")
    except Exception as e:
        logger.warning("tracing_instrumentor_fail name=fastapi error=%s", e)


def _instrument_aiohttp() -> None:
    try:
        from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor

        AioHttpClientInstrumentor().instrument()
        logger.info("tracing_instrumentor_ok name=aiohttp_client")
    except Exception as e:
        logger.warning("tracing_instrumentor_fail name=aiohttp_client error=%s", e)


def _instrument_httpx() -> None:
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        logger.info("tracing_instrumentor_ok name=httpx")
    except Exception as e:
        logger.warning("tracing_instrumentor_fail name=httpx error=%s", e)


def _instrument_sqlalchemy(settings) -> None:
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        from app.db.database import engine

        SQLAlchemyInstrumentor().instrument(
            engine=engine,
            enable_commenter=True,
        )
        logger.info("tracing_instrumentor_ok name=sqlalchemy")
    except Exception as e:
        logger.warning("tracing_instrumentor_fail name=sqlalchemy error=%s", e)
