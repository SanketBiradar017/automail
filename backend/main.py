from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

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
from backend.scheduler import start_scheduler, shutdown_scheduler


app = FastAPI(
    title="MailGenie AI",
    description="AI-powered Gmail Email Automation",
    version="1.0.0"
)

init_db()

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
    start_scheduler()


@app.on_event("shutdown")
def _on_shutdown():
    shutdown_scheduler()


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