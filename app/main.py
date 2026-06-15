import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import OperationalError
from starlette.staticfiles import StaticFiles

from app.api import agents, capabilities, channels, chat, chat_agent_draft, chat_handoff, chat_pending, conversations, datasources, functions, knowledge, knowledge_packs, onboarding, schedules, settings as settings_api, skills, sql_analysis
from app.core.config import get_settings
from app.core.logging import configure_logging, fmt_kv, get_logger
from app.db.database import init_db
from app.services.scheduler.runtime_state import get_scheduler_worker, set_scheduler_worker
from app.services.scheduler.worker import SchedulerWorker

settings = get_settings()
logger = get_logger("app.main")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HANDBOOK_SITE = _REPO_ROOT / "site_handbook"

app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Tracing — EE-only, skip if module absent
try:
    from app.tracing import init_tracing, shutdown_tracing
    init_tracing(settings, app=app)
    _has_tracing = True
except ImportError:
    _has_tracing = False


@app.on_event("startup")
async def startup():
    configure_logging(settings.debug)
    if _HANDBOOK_SITE.is_dir():
        logger.info("handbook_static_mounted path=%s", _HANDBOOK_SITE)
    else:
        logger.warning(
            "handbook_static_missing path=%s run: make handbook-build",
            _HANDBOOK_SITE,
        )
    init_db()
    # CE built-in functions + schedules
    try:
        from app.builtin_functions import register_builtin_functions
        from app.db.database import SessionLocal
        with SessionLocal() as _seed_db:
            outcome = register_builtin_functions(_seed_db)
            logger.info("builtin_functions_bootstrap_done created=%s updated=%s", outcome.get("created", 0), outcome.get("updated", 0))
    except Exception as exc:
        logger.warning("builtin_functions_bootstrap_failed error=%s", exc)
    # EE: stats_analysis schedule bootstrap
    try:
        from app.api import stats_analysis
        outcome = stats_analysis.ensure_stats_analysis_schedule_singleton()
        logger.info(
            "stats_analysis_schedule_bootstrap_done created=%s removed_legacy=%s",
            outcome.get("created", 0),
            outcome.get("removed_legacy", 0),
        )
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("stats_analysis_schedule_bootstrap_failed error=%s", exc)
    onboarding_done = False
    if settings.scheduler_autostart:
        try:
            from app.api.onboarding import is_onboarding_completed
            from app.db.database import SessionLocal
            with SessionLocal() as _db:
                onboarding_done = is_onboarding_completed(_db)
        except OperationalError:
            onboarding_done = True  # table not yet migrated — allow scheduler to start

    if settings.scheduler_autostart and onboarding_done:
        worker = SchedulerWorker(
            refresh_interval_seconds=settings.scheduler_refresh_interval_seconds,
            job_coalesce=settings.scheduler_job_coalesce,
            job_misfire_grace_seconds=settings.scheduler_job_misfire_grace_seconds,
            job_max_instances=settings.scheduler_job_max_instances,
        )
        await worker.start()
        set_scheduler_worker(worker)
        logger.info("scheduler_runtime_autostart_enabled")
    elif settings.scheduler_autostart and not onboarding_done:
        set_scheduler_worker(None)
        logger.info("scheduler_deferred_pending_onboarding")
    else:
        set_scheduler_worker(None)
        logger.info("scheduler_runtime_autostart_disabled")


@app.on_event("shutdown")
async def shutdown():
    if _has_tracing:
        shutdown_tracing()
    worker = get_scheduler_worker()
    if worker is None:
        return
    await worker.shutdown()
    set_scheduler_worker(None)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    serialized_errors = json.loads(json.dumps(exc.errors(), default=str, ensure_ascii=False))
    logger.error(
        "request_validation_error %s detail=%s",
        fmt_kv(method=request.method, path=request.url.path),
        serialized_errors,
    )
    first_error = serialized_errors[0] if serialized_errors else {}
    message = first_error.get("msg", "Request validation failed")
    return JSONResponse(
        status_code=422,
        content={"detail": message, "errors": serialized_errors},
    )


# ── CE routers (always registered) ──────────────────────────────────────────
app.include_router(datasources.router, prefix="/api/v1")
app.include_router(knowledge.router, prefix="/api/v1")
app.include_router(knowledge_packs.router, prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")
app.include_router(conversations.message_router, prefix="/api/v1")
app.include_router(agents.router, prefix="/api/v1")
app.include_router(skills.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(chat_handoff.router, prefix="/api/v1")
app.include_router(chat_pending.router, prefix="/api/v1")
app.include_router(chat_agent_draft.router, prefix="/api/v1")
app.include_router(functions.router, prefix="/api/v1")
app.include_router(schedules.router, prefix="/api/v1")
app.include_router(channels.router, prefix="/api/v1")
app.include_router(sql_analysis.router, prefix="/api/v1")
app.include_router(capabilities.router, prefix="/api/v1")
app.include_router(onboarding.router, prefix="/api/v1")
app.include_router(settings_api.router, prefix="/api/v1")

# ── EE routers (skip if modules absent) ─────────────────────────────────────
_ee_api_modules = [
    "collector", "pages", "services", "sessions",
    "sql_analysis_monitor", "stats_analysis", "traces",
]
for _mod_name in _ee_api_modules:
    try:
        _mod = __import__(f"app.api.{_mod_name}", fromlist=["router"])
        app.include_router(_mod.router, prefix="/api/v1")
    except ImportError:
        logger.debug("ee_router_skipped module=%s", _mod_name)


@app.get("/health")
def health_check():
    return {"status": "ok"}


if _HANDBOOK_SITE.is_dir():
    app.mount(
        "/handbook",
        StaticFiles(directory=str(_HANDBOOK_SITE), html=True),
        name="handbook",
    )

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    from starlette.responses import FileResponse as _FR
    from starlette.staticfiles import StaticFiles as _SF

    _index_html = str(_FRONTEND_DIST / "index.html")
    app.mount("/assets", _SF(directory=str(_FRONTEND_DIST / "assets")), name="frontend-assets")

    @app.get("/{full_path:path}")
    async def _spa_fallback(full_path: str):
        file = _FRONTEND_DIST / full_path
        if file.is_file():
            return _FR(str(file))
        return _FR(_index_html)
