"""
Journal d'audit : qui a fait quoi, quand, depuis quelle adresse.

On enregistre des ÉVÉNEMENTS (connexion, création de compte, envoi d'un message, dépôt d'un fichier, réglage modifié…),
jamais le contenu des conversations. Le nom de l'acteur est copié dans la ligne : la trace survit à la suppression du compte.
"""
import json
import logging

from fastapi import Request
from sqlalchemy.orm import Session

from .models import AuditLog, now_ms

log = logging.getLogger("opti.audit")

# Catégories (préfixe de l'action) proposées comme filtres dans l'administration
CATEGORIES = {
    "auth": "Connexions",
    "user": "Comptes",
    "settings": "Réglages",
    "chat": "Conversations",
    "file": "Fichiers",
    "data": "Données personnelles",
    "system": "Système",
}


def client_ip(request: Request | None) -> str:
    return (request.client.host if request and request.client else "")[:64]


def record(db: Session, action: str, *, actor=None, actor_name: str = "", target: str = "",
           detail: dict | None = None, request: Request | None = None, commit: bool = True) -> None:
    """Ajoute une ligne au journal. Ne doit jamais faire échouer l'action qu'elle décrit."""
    try:
        db.add(AuditLog(
            actor_id=getattr(actor, "id", None),
            actor_name=(getattr(actor, "username", "") or actor_name)[:200],
            action=action[:64], target=target[:300],
            detail=json.dumps(detail, ensure_ascii=False)[:4000] if detail else "",
            ip=client_ip(request), ts=now_ms()))
        if commit:
            db.commit()
    except Exception:
        db.rollback()
        log.exception("Écriture du journal d'audit impossible (%s)", action)
