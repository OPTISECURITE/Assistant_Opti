"""
API des conversations : lister, créer, lire, renommer, épingler, supprimer,
envoyer un message (réponse en streaming, enregistrée en base) et régénérer.
"""
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import agent, audit, config, settings
from .auth import CurrentUser, current_user
from .db import SessionLocal, get_db
from .files import delete_files_of_chat, file_summary
from .models import Chat, File, Message, now_ms

log = logging.getLogger("opti.chats")
router = APIRouter(prefix="/api/chats", tags=["chats"])

TITLE_MAX = 60


# ── Schémas ──────────────────────────────────────────────────────────────────
class ChatUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    pinned: bool | None = None


class NewMessage(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    file_ids: list[str] = Field(default_factory=list, max_length=10)
    web_mode: str = Field(default="auto", pattern="^(auto|on|off)$")


class Regenerate(BaseModel):
    web_mode: str = Field(default="auto", pattern="^(auto|on|off)$")


def chat_summary(c: Chat) -> dict:
    return {"id": c.id, "title": c.title, "pinned": c.pinned,
            "created_at": c.created_at, "updated_at": c.updated_at}


def chat_full(c: Chat, db: Session) -> dict:
    by_message: dict[str, list] = {}
    for f in db.scalars(select(File).where(File.chat_id == c.id).order_by(File.created_at)):
        by_message.setdefault(f.message_id, []).append(file_summary(f))
    return {**chat_summary(c), "messages": [
        {"id": m.id, "role": m.role, "content": m.content, "done": m.done, "model": m.model,
         "files": by_message.get(m.id, [])}
        for m in c.messages
    ]}


def get_owned_chat(db: Session, chat_id: str, user: CurrentUser) -> Chat:
    chat = db.get(Chat, chat_id)
    if not chat or chat.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation introuvable")
    return chat


def make_title(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= TITLE_MAX else text[:TITLE_MAX].rstrip() + "…"


# ── Lecture / gestion ────────────────────────────────────────────────────────
@router.get("")
def list_chats(q: str | None = Query(default=None, max_length=200),
               user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(Chat).where(Chat.owner_id == user.id)
    if q:
        like = f"%{q.strip()}%"
        in_messages = select(Message.chat_id).where(Message.content.ilike(like))
        stmt = stmt.where(or_(Chat.title.ilike(like), Chat.id.in_(in_messages)))
    stmt = stmt.order_by(Chat.pinned.desc(), Chat.updated_at.desc()).limit(500)
    return [chat_summary(c) for c in db.scalars(stmt)]


@router.post("", status_code=201)
def create_chat(user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    chat = Chat(owner_id=user.id)
    db.add(chat)
    db.commit()
    return chat_summary(chat)


@router.get("/{chat_id}")
def read_chat(chat_id: str, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    return chat_full(get_owned_chat(db, chat_id, user), db)


@router.patch("/{chat_id}")
def update_chat(chat_id: str, body: ChatUpdate,
                user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    chat = get_owned_chat(db, chat_id, user)
    if body.title is not None:
        chat.title = body.title.strip()
    if body.pinned is not None:
        chat.pinned = body.pinned
    db.commit()
    return chat_summary(chat)


@router.delete("/{chat_id}", status_code=204)
def delete_chat(chat_id: str, request: Request, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    chat = get_owned_chat(db, chat_id, user)
    delete_files_of_chat(db, chat.id)
    db.delete(chat)
    db.commit()
    audit.record(db, "chat.delete", actor=user, target=chat_id, request=request)


# ── Génération ───────────────────────────────────────────────────────────────
async def stream_and_store(chat_id: str, web_mode: str = "auto") -> AsyncIterator[str]:
    """
    Envoie l'historique à Ollama et enregistre la réponse au fil de l'eau.
    Si l'utilisateur interrompt (bouton stop / fermeture de l'onglet), la partie
    déjà reçue est conservée et marquée comme interrompue.
    """
    db = SessionLocal()
    try:
        chat = db.get(Chat, chat_id)
        files = db.scalars(select(File).where(File.chat_id == chat_id).order_by(File.created_at)).all()
        answer = Message(chat_id=chat_id, position=len(chat.messages), role="assistant",
                         content="", model=settings.app().model, done=False)
        db.add(answer)
        db.commit()

        parts: list[str] = []
        completed = False
        tokens = agent.run(chat, files, web_mode)
        try:
            async for token in tokens:
                parts.append(token)
                yield token
            completed = True
        finally:
            await tokens.aclose()   # coupe la requête Ollama et l'analyse en cours
            answer.content = "".join(parts)
            answer.done = completed
            chat.updated_at = now_ms()
            if not answer.content:
                db.delete(answer)
            db.commit()
    finally:
        db.close()


def streaming(chat_id: str, web_mode: str = "auto") -> StreamingResponse:
    return StreamingResponse(
        stream_and_store(chat_id, web_mode),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{chat_id}/messages")
def send_message(chat_id: str, body: NewMessage, request: Request,
                 user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    chat = get_owned_chat(db, chat_id, user)
    files = []
    for fid in dict.fromkeys(body.file_ids):
        f = db.get(File, fid)
        if not f or f.owner_id != user.id or f.message_id:
            raise HTTPException(status_code=400, detail="Fichier joint invalide.")
        files.append(f)
    if not chat.messages:
        chat.title = make_title(body.content)
    msg = Message(chat_id=chat.id, position=len(chat.messages), role="user", content=body.content)
    db.add(msg)
    db.flush()
    for f in files:
        f.chat_id, f.message_id = chat.id, msg.id
    chat.updated_at = now_ms()
    db.commit()
    audit.record(db, "chat.message", actor=user, target=chat.id, request=request,
                 detail={"files": len(files), "web": body.web_mode})   # jamais le texte du message
    return streaming(chat.id, body.web_mode)


@router.post("/{chat_id}/regenerate")
def regenerate(chat_id: str, body: Regenerate | None = None,
               user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    chat = get_owned_chat(db, chat_id, user)
    if not chat.messages:
        raise HTTPException(status_code=400, detail="Conversation vide")
    last = chat.messages[-1]
    if last.role == "assistant":
        db.delete(last)
        db.commit()
    return streaming(chat.id, body.web_mode if body else "auto")
