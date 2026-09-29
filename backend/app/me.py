"""Espace personnel : préférences, export et suppression de ses conversations."""
import json
import time

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import settings
from .auth import CurrentUser, current_user
from .db import get_db
from .files import delete_files_of_chat
from .models import Chat, File

router = APIRouter(prefix="/api/me", tags=["me"])


@router.get("/settings")
def get_settings(user: CurrentUser = Depends(current_user)):
    return settings.user_prefs(user.id)


@router.put("/settings")
def put_settings(prefs: settings.UserPrefs, user: CurrentUser = Depends(current_user)):
    return settings.save_user_prefs(user.id, prefs)


@router.get("/export")
def export(user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    chats = db.scalars(select(Chat).where(Chat.owner_id == user.id).order_by(Chat.created_at)).all()
    files = db.scalars(select(File).where(File.owner_id == user.id, File.chat_id.is_not(None))).all()
    names: dict[str, list[str]] = {}
    for f in files:
        names.setdefault(f.message_id, []).append(f.filename)
    data = {
        "exporte_le": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "utilisateur": user.username,
        "conversations": [{
            "titre": c.title,
            "epinglee": c.pinned,
            "creee_le": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(c.created_at / 1000)),
            "messages": [{"role": "utilisateur" if m.role == "user" else "assistant",
                          "contenu": m.content, "fichiers": names.get(m.id, [])} for m in c.messages],
        } for c in chats],
    }
    body = json.dumps(data, ensure_ascii=False, indent=2)
    filename = f"assistant-opti-{user.username}-{time.strftime('%Y%m%d')}.json"
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.delete("/chats", status_code=204)
def delete_all(user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    for chat in db.scalars(select(Chat).where(Chat.owner_id == user.id)).all():
        delete_files_of_chat(db, chat.id)
        db.delete(chat)
    db.commit()
