from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.routes.email_routes import router
from backend.routes.schedule_routes import router as schedule_router
from backend.routes.followup_routes import router as followup_router
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


@app.on_event("startup")
def _on_startup():
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