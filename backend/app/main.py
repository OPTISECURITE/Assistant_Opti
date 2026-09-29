"""
Assistant Opti — point d'entrée de l'API.

Lancement (depuis /opt/assistant-opti) :
    venv/bin/uvicorn --app-dir backend app.main:app --host 0.0.0.0 --port 8100
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import admin, auth_routes, chats, config, files, me, settings
from .auth import current_user
from .db import SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    config.FILES_DIR.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        n = files.purge_orphans(db)
        if n:
            logging.getLogger("opti").info("%d fichier(s) orphelin(s) supprimé(s)", n)
    yield


app = FastAPI(title="Assistant Opti", docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(auth_routes.router)
app.include_router(chats.router)
app.include_router(files.router)
app.include_router(me.router)
app.include_router(admin.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/config", dependencies=[Depends(current_user)])
async def get_config():
    s = settings.app()
    return {"model": s.model, "model_label": s.model_label, "web_enabled": s.web_enabled,
            "uploads_enabled": s.uploads_enabled, "analysis_enabled": s.analysis_enabled,
            "max_upload_mb": s.max_upload_mb}


# ── Front ────────────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")


@app.get("/")
@app.get("/c/{chat_id}")
async def index(chat_id: str | None = None):
    # /c/<id> : ouvre directement une conversation (lien, rechargement de page)
    return FileResponse(config.FRONTEND_DIR / "index.html")
