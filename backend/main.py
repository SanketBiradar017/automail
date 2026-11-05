from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from backend.routes.email_routes import router
from backend.routes.schedule_routes import router as schedule_router
from backend.routes.followup_routes import router as followup_router
from backend.routes.auth_routes import router as auth_router
from backend.routes.analytics_routes import router as analytics_router
from backend.routes.reply_routes import router as reply_router
from backend.routes.inbox_routes import router as inbox_router
from backend.routes.template_routes import router as template_router
from backend.routes.avatar_routes import router as avatar_router
from backend.routes.tracking_routes import router as tracking_router
from backend.database import init_db
from backend.admin import models as _admin_models  # noqa: F401  (register admin tables)
from backend.admin.routes_core import auth_router as admin_auth_router, router as admin_router
from backend.admin.routes_ops import router as admin_ops_router
from backend.admin import telemetry
from backend.admin.security import bootstrap_first_admin
from backend.scheduler import start_scheduler, shutdown_scheduler


app = FastAPI(
    title="MailGenie AI",
    description="AI-powered Gmail Email Automation",
    version="1.0.0"
)

init_db()
telemetry.install_error_logging()

app.include_router(router)
app.include_router(schedule_router)
app.include_router(followup_router)
app.include_router(auth_router)
app.include_router(analytics_router)
app.include_router(reply_router)
app.include_router(inbox_router)
app.include_router(template_router)
app.include_router(avatar_router)
app.include_router(tracking_router)
app.include_router(admin_auth_router)
app.include_router(admin_router)
app.include_router(admin_ops_router)


@app.on_event("startup")
def _on_startup():
    try:  # give pre-existing emails their status events (idempotent, no network)
        from backend.database import SessionLocal
        from backend.gmail_auth import get_connected_email
        from backend import email_status
        _db = SessionLocal()
        try:
            email_status.backfill(_db, get_connected_email())
        finally:
            _db.close()
    except Exception as exc:
        print(f"[startup] status backfill skipped: {exc}")
    try:
        from backend.database import SessionLocal as _SL
        _d = _SL()
        try:
            bootstrap_first_admin(_d)
        finally:
            _d.close()
        telemetry.backfill_users()
    except Exception as exc:
        print(f"[startup] admin setup skipped: {exc}")
    start_scheduler()


@app.on_event("shutdown")
def _on_shutdown():
    shutdown_scheduler()


@app.exception_handler(Exception)
async def _unhandled(request, exc):
    telemetry.record_error("ERROR", "unhandled", f"{type(exc).__name__}: {exc}", path=request.url.path, status_code=500, exc=exc)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


@app.middleware("http")
async def _log_server_errors(request, call_next):
    response = await call_next(request)
    if response.status_code >= 500 and not request.url.path.startswith("/static"):
        telemetry.record_error("ERROR", "http", f"{request.method} {request.url.path} -> {response.status_code}",
                               path=request.url.path, status_code=response.status_code)
    return response


app.mount(
    "/static",
    StaticFiles(directory="frontend"),
    name="static"
)


@app.get("/")
def home():

    return FileResponse(
        "frontend/index.html"
    )


@app.get("/admin", include_in_schema=False)
def admin_panel():
    return FileResponse("frontend/admin/index.html", headers={
        "Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
    })
