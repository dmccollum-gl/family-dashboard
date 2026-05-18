from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from config import settings
from database import init_db
import routers.system_settings as system_settings_router
import routers.user_prefs as user_prefs_router
import routers.auth as auth_router
import routers.weather as weather_router
import routers.rss as rss_router
import routers.calendar as calendar_router
import routers.system_info as system_info_router

app = FastAPI(title="Dashboard")

init_db()

app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:5173", "http://localhost:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system_settings_router.router, prefix="/api/settings", tags=["Settings"])
app.include_router(user_prefs_router.router, prefix="/api/user-prefs", tags=["UserPrefs"])
app.include_router(auth_router.router,       prefix="/api/auth",       tags=["Auth"])
app.include_router(weather_router.router,   prefix="/api/weather",    tags=["Weather"])
app.include_router(rss_router.router,       prefix="/api/rss",        tags=["RSS"])
app.include_router(calendar_router.router,  prefix="/api/calendar",   tags=["Calendar"])
app.include_router(system_info_router.router, prefix="/api/system",   tags=["System"])


@app.get("/api/health")
def health():
    return {"status": "ok"}
