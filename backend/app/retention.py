"""
Conservation des données : suppression automatique des conversations inactives (RGPD) et purge du journal d'audit.

La règle est fixée dans l'administration (0 jour = conservation illimitée). Le contrôle tourne toutes les heures.
"""
import asyncio
import logging

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import audit, settings
from .db import SessionLocal
from .files import delete_files_of_chat, purge_orphans
from .models import AuditLog, Chat, now_ms

log = logging.getLogger("opti.retention")
DAY = 86_400_000


def _expired(days: int, keep_pinned: bool):
    stmt = select(Chat).where(Chat.updated_at < now_ms() - days * DAY)
    if keep_pinned:
        stmt = stmt.where(Chat.pinned.is_(False))
    return stmt


def preview(db: Session, days: int, keep_pinned: bool) -> int:
    """Nombre de conversations qu'une règle de `days` jours supprimerait immédiatement."""
    if days <= 0:
        return 0
    return db.scalar(select(func.count()).select_from(_expired(days, keep_pinned).subquery())) or 0


def purge() -> dict:
    s = settings.app()
    out = {"chats": 0, "orphan_files": 0, "audit": 0}
    with SessionLocal() as db:
        if s.retention_days > 0:
            chats = db.scalars(_expired(s.retention_days, s.retention_keep_pinned)).all()
            for chat in chats:
                delete_files_of_chat(db, chat.id)
                db.delete(chat)
            db.commit()
            out["chats"] = len(chats)
        out["orphan_files"] = purge_orphans(db)
        cutoff = now_ms() - s.audit_retention_days * DAY
        out["audit"] = db.execute(delete(AuditLog).where(AuditLog.ts < cutoff)).rowcount or 0
        db.commit()
        if out["chats"] or out["audit"]:
            audit.record(db, "system.retention_purge", actor_name="système", detail=out)
    return out


async def loop() -> None:
    await asyncio.sleep(30)
    while True:
        try:
            out = await asyncio.to_thread(purge)
            if out["chats"] or out["audit"]:
                log.info("Purge : %s", out)
        except Exception:
            log.exception("Purge de conservation impossible")
        await asyncio.sleep(3600)
