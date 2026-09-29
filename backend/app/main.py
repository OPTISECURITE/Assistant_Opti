"""
Assistant Opti — point d'entrée de l'API.

Lancement (depuis /opt/assistant-opti) :
    venv/bin/uvicorn --app-dir backend app.main:app --host 0.0.0.0 --port 8100
"""
import logging

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, ollama

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")

app = FastAPI(title="Assistant Opti", docs_url=None, redoc_url=None)


class Message(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=100_000)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=200)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/config")
async def get_config():
    return {"model": config.MODEL, "model_label": config.MODEL_LABEL}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    messages = [m.model_dump() for m in req.messages]
    return StreamingResponse(
        ollama.stream_chat(messages),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Front ────────────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")
