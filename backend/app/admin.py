"""Administration : comptes, réglages globaux, modèles disponibles, statistiques."""
import csv
import datetime
import io
import shutil

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DbSession

from . import audit, config, retention, settings
from .scheduler import scheduler
from .auth import CurrentUser, current_user, hash_password
from .db import get_db
from .files import delete_files_of_chat
from .models import AuditLog, Chat, File, Message, Session, User, now_ms

MIN_PASSWORD = 12


def require_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Réservé aux administrateurs")
    return user


router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


# ── Comptes ──────────────────────────────────────────────────────────────────
class NewUser(BaseModel):
    username: str = Field(min_length=2, max_length=150, pattern=r"^[A-Za-z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=MIN_PASSWORD, max_length=512)
    is_admin: bool = False


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    password: str | None = Field(default=None, min_length=MIN_PASSWORD, max_length=512)
    is_admin: bool | None = None
    active: bool | None = None


def user_row(u: User, chats: int) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "source": u.source,
            "is_admin": u.is_admin, "active": u.active, "created_at": u.created_at,
            "last_login_at": u.last_login_at, "chats": chats}


@router.get("/users")
def list_users(db: DbSession = Depends(get_db)):
    counts = dict(db.execute(select(Chat.owner_id, func.count()).group_by(Chat.owner_id)).all())
    return [user_row(u, counts.get(u.id, 0)) for u in db.scalars(select(User).order_by(User.username))]


@router.post("/users", status_code=201)
def create_user(body: NewUser, request: Request, me: CurrentUser = Depends(require_admin), db: DbSession = Depends(get_db)):
    username = body.username.lower()
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(409, "Cet identifiant existe déjà.")
    u = User(username=username, display_name=body.display_name.strip(),
             password_hash=hash_password(body.password), is_admin=body.is_admin)
    db.add(u)
    db.commit()
    audit.record(db, "user.create", actor=me, target=username, request=request, detail={"admin": body.is_admin})
    return user_row(u, 0)


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserUpdate, request: Request, me: CurrentUser = Depends(require_admin),
                db: DbSession = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "Compte introuvable")
    if u.id == me.id and (body.active is False or body.is_admin is False):
        raise HTTPException(400, "Vous ne pouvez pas désactiver votre propre compte ni retirer vos droits d'administrateur.")
    if body.display_name is not None:
        u.display_name = body.display_name.strip()
    if body.is_admin is not None:
        u.is_admin = body.is_admin
    if body.active is not None:
        u.active = body.active
        if not body.active:
            db.execute(delete(Session).where(Session.user_id == u.id))
    if body.password is not None:
        u.password_hash = hash_password(body.password)
        db.execute(delete(Session).where(Session.user_id == u.id))   # déconnecté partout
    db.commit()
    changed = {k: (getattr(body, k) if k != "password" else "modifié") for k in body.model_fields_set}   # jamais la valeur du mot de passe
    audit.record(db, "user.update", actor=me, target=u.username, request=request, detail=changed)
    return user_row(u, db.scalar(select(func.count()).select_from(Chat).where(Chat.owner_id == u.id)))


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: str, request: Request, me: CurrentUser = Depends(require_admin), db: DbSession = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "Compte introuvable")
    if u.id == me.id:
        raise HTTPException(400, "Vous ne pouvez pas supprimer votre propre compte.")
    for chat in db.scalars(select(Chat).where(Chat.owner_id == u.id)).all():
        delete_files_of_chat(db, chat.id)
        db.delete(chat)
    for f in db.scalars(select(File).where(File.owner_id == u.id)).all():
        shutil.rmtree(config.FILES_DIR / f.id, ignore_errors=True)
        db.delete(f)
    username = u.username
    db.delete(u)
    db.commit()
    audit.record(db, "user.delete", actor=me, target=username, request=request)


# ── Réglages globaux ─────────────────────────────────────────────────────────
@router.get("/settings")
def get_app_settings():
    return {"values": settings.app(), "defaults": settings.AppSettings()}


@router.put("/settings")
def put_app_settings(body: settings.AppSettings, request: Request, me: CurrentUser = Depends(require_admin),
                     db: DbSession = Depends(get_db)):
    old = settings.app().model_dump()
    saved = settings.save_app(body)
    diff = {}
    for key, new in saved.model_dump().items():
        if new != old.get(key):
            diff[key] = "modifiée" if key == "system_prompt" else {"avant": old.get(key), "après": new}
    if diff:
        audit.record(db, "settings.update", actor=me, request=request, detail=diff)
    return saved


@router.get("/models")
async def ollama_models():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{config.OLLAMA_URL}/api/tags")
            r.raise_for_status()
        return sorted(m["name"] for m in r.json().get("models", []))
    except Exception:
        return [settings.app().model]


# ── Statistiques ─────────────────────────────────────────────────────────────
@router.get("/stats")
def stats(db: DbSession = Depends(get_db)):
    now = now_ms()
    day = 86_400_000
    since7, since30 = now - 7 * day, now - 30 * day

    active7 = db.scalar(select(func.count(func.distinct(Chat.owner_id)))
                        .select_from(Message).join(Chat, Chat.id == Message.chat_id)
                        .where(Message.role == "user", Message.created_at >= since7)) or 0

    # messages envoyés par jour (14 derniers jours)
    today = datetime.date.today()
    start = datetime.datetime.combine(today - datetime.timedelta(days=13), datetime.time()).timestamp() * 1000
    per_day = {(today - datetime.timedelta(days=i)).isoformat(): 0 for i in range(14)}
    for (ts,) in db.execute(select(Message.created_at).where(Message.role == "user", Message.created_at >= start)):
        d = datetime.date.fromtimestamp(ts / 1000).isoformat()
        if d in per_day:
            per_day[d] += 1

    answers30 = select(Message.content).where(Message.role == "assistant", Message.created_at >= since30)
    searches = analyses = 0
    for (content,) in db.execute(answers30):
        searches += "```recherche" in content
        analyses += content.count("```resultat")

    top = db.execute(
        select(User.display_name, func.count(Message.id))
        .join(Chat, Chat.owner_id == User.id).join(Message, Message.chat_id == Chat.id)
        .where(Message.role == "user", Message.created_at >= since30)
        .group_by(User.id).order_by(func.count(Message.id).desc()).limit(5)).all()

    return {
        "users": db.scalar(select(func.count()).select_from(User)) or 0,
        "active_users_7d": active7,
        "chats": db.scalar(select(func.count()).select_from(Chat)) or 0,
        "messages_30d": db.scalar(select(func.count()).select_from(Message)
                                  .where(Message.role == "user", Message.created_at >= since30)) or 0,
        "searches_30d": searches,
        "analyses_30d": analyses,
        "files_30d": db.scalar(select(func.count()).select_from(File).where(File.created_at >= since30)) or 0,
        "per_day": [{"day": d, "messages": n} for d, n in sorted(per_day.items())],
        "top_users_30d": [{"name": n, "messages": c} for n, c in top],
    }


# ── Charge du GPU ────────────────────────────────────────────────────────────
@router.get("/load")
def load():
    return scheduler.snapshot()


# ── Conservation des données ─────────────────────────────────────────────────
class RetentionPreview(BaseModel):
    days: int = Field(ge=0, le=3650)
    keep_pinned: bool = True


@router.post("/retention/preview")
def retention_preview(body: RetentionPreview, db: DbSession = Depends(get_db)):
    return {"chats": retention.preview(db, body.days, body.keep_pinned)}


@router.post("/retention/run")
def retention_run(request: Request, me: CurrentUser = Depends(require_admin), db: DbSession = Depends(get_db)):
    out = retention.purge()
    audit.record(db, "system.retention_run", actor=me, request=request, detail=out)
    return out


# ── Journal d'audit ──────────────────────────────────────────────────────────
def _audit_query(category: str | None, q: str | None):
    stmt = select(AuditLog)
    if category:
        stmt = stmt.where(AuditLog.action.like(f"{category}.%"))
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where((AuditLog.actor_name.ilike(like)) | (AuditLog.target.ilike(like)) | (AuditLog.action.ilike(like)))
    return stmt


def _audit_row(r: AuditLog) -> dict:
    return {"id": r.id, "ts": r.ts, "actor": r.actor_name, "action": r.action, "target": r.target,
            "detail": r.detail, "ip": r.ip}


@router.get("/audit")
def audit_list(category: str | None = Query(default=None, max_length=20), q: str | None = Query(default=None, max_length=100),
               before: int | None = None, limit: int = Query(default=50, ge=1, le=200), db: DbSession = Depends(get_db)):
    stmt = _audit_query(category, q)
    if before:
        stmt = stmt.where(AuditLog.id < before)
    rows = db.scalars(stmt.order_by(AuditLog.id.desc()).limit(limit + 1)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return {"rows": [_audit_row(r) for r in rows], "next": rows[-1].id if more and rows else None,
            "categories": audit.CATEGORIES}


def _csv_safe(value: str) -> str:
    """Empêche l'exécution de formules quand le fichier est ouvert dans Excel (les cibles et identifiants viennent de saisies)."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


@router.get("/audit/export.csv")
def audit_export(request: Request, category: str | None = Query(default=None, max_length=20),
                 q: str | None = Query(default=None, max_length=100), me: CurrentUser = Depends(require_admin),
                 db: DbSession = Depends(get_db)):
    rows = db.scalars(_audit_query(category, q).order_by(AuditLog.id.desc()).limit(100_000)).all()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Date", "Acteur", "Action", "Cible", "Détail", "Adresse IP"])
    for r in rows:
        w.writerow([datetime.datetime.fromtimestamp(r.ts / 1000).strftime("%d/%m/%Y %H:%M:%S"),
                    _csv_safe(r.actor_name), r.action, _csv_safe(r.target), _csv_safe(r.detail), r.ip])
    audit.record(db, "system.audit_export", actor=me, request=request, detail={"rows": len(rows)})
    name = f"journal-audit-{datetime.date.today().isoformat()}.csv"
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
