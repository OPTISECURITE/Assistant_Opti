"""Routes des connexions API : gestion par l'administrateur, liste pour l'utilisateur."""
import json
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import audit, connections as C, settings, vault
from .admin import require_admin
from .auth import CurrentUser, current_user
from .db import get_db
from .models import ApiConnection, ApiGrant, User, now_ms

router = APIRouter(prefix="/api/admin/connections", tags=["connections"], dependencies=[Depends(require_admin)])
user_router = APIRouter(prefix="/api/connections", tags=["connections"])


class ConnectionIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    description: str = Field(default="", max_length=600)
    base_url: str
    auth_type: Literal["none", "bearer", "header", "basic"] = "none"
    auth_header: str = Field(default="", max_length=100, pattern=r"^[A-Za-z0-9\-_]*$")
    username: str = Field(default="", max_length=200)
    secret: str | None = Field(default=None, max_length=4000)      # None : inchangé ; "" : effacé
    verify_tls: bool = True
    timeout_s: int = Field(default=15, ge=3, le=60)
    test_path: str = Field(default="/", max_length=300)
    enabled: bool = True
    allow_all: bool = False
    users: list[str] = Field(default_factory=list, max_length=500)
    operations: list[C.Operation] = Field(default_factory=list, max_length=300)

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str) -> str:
        return C.check_base_url(v)

    @field_validator("test_path")
    @classmethod
    def _test_path(cls, v: str) -> str:
        v = v.strip() or "/"
        if "://" in v or ".." in v or not re.fullmatch(r"/?[A-Za-z0-9_\-./~:%@?=&]*", v):
            raise ValueError("chemin de test invalide")
        return v if v.startswith("/") else "/" + v

    @model_validator(mode="after")
    def _coherent(self):
        if self.auth_type == "header" and not self.auth_header:
            raise ValueError("indiquez le nom de l'en-tête qui porte la clé (par exemple X-API-Key)")
        ids = [o.id for o in self.operations]
        if len(set(ids)) != len(ids):
            raise ValueError("deux opérations portent le même identifiant")
        return self


class ImportIn(BaseModel):
    url: str | None = Field(default=None, max_length=1000)
    text: str | None = Field(default=None, max_length=3_000_000)
    connection_id: str | None = None


class TryIn(BaseModel):
    operation: str = Field(max_length=60)
    params: dict = Field(default_factory=dict)


def row(conn: ApiConnection, db: Session) -> dict:
    ops = C.operations_of(conn)
    return {
        "id": conn.id, "name": conn.name, "slug": conn.slug, "description": conn.description, "base_url": conn.base_url,
        "auth_type": conn.auth_type, "auth_header": conn.auth_header, "username": conn.username,
        "has_secret": bool(conn.secret_enc),                       # jamais le secret lui-même
        "verify_tls": conn.verify_tls, "timeout_s": conn.timeout_s, "test_path": conn.test_path, "enabled": conn.enabled,
        "allow_all": conn.allow_all, "users": sorted(C.granted_ids_of_connection(db, conn.id)),
        "operations": [o.model_dump() for o in ops], "ops_enabled": sum(1 for o in ops if o.enabled),
        "created_at": conn.created_at, "updated_at": conn.updated_at,
    }


def _get(db: Session, conn_id: str) -> ApiConnection:
    conn = db.get(ApiConnection, conn_id)
    if not conn:
        raise HTTPException(404, "Connexion introuvable")
    return conn


def _apply(conn: ApiConnection, body: ConnectionIn) -> None:
    conn.name, conn.description, conn.base_url = body.name.strip(), body.description.strip(), body.base_url
    conn.auth_type, conn.auth_header, conn.username = body.auth_type, body.auth_header.strip(), body.username.strip()
    conn.verify_tls, conn.timeout_s, conn.test_path = body.verify_tls, body.timeout_s, body.test_path
    conn.enabled, conn.allow_all = body.enabled, body.allow_all
    conn.operations = json.dumps([o.model_dump() for o in body.operations], ensure_ascii=False)
    if body.auth_type == "none":
        conn.secret_enc = ""
    elif body.secret is not None:
        conn.secret_enc = vault.encrypt(body.secret)
    conn.updated_at = now_ms()


def _set_grants(db: Session, conn: ApiConnection, user_ids: list[str]) -> None:
    valid = set(db.scalars(select(User.id).where(User.id.in_(user_ids))).all()) if user_ids else set()
    db.execute(delete(ApiGrant).where(ApiGrant.connection_id == conn.id))
    for uid in valid:
        db.add(ApiGrant(connection_id=conn.id, user_id=uid))


@router.get("")
def list_connections(db: Session = Depends(get_db)):
    return [row(c, db) for c in db.scalars(select(ApiConnection).order_by(ApiConnection.name)).all()]


@router.post("", status_code=201)
def create(body: ConnectionIn, request: Request, me: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    if db.scalar(select(ApiConnection).where(ApiConnection.name.ilike(body.name.strip()))):
        raise HTTPException(409, "Une connexion porte déjà ce nom.")
    base = C.slugify(body.name)
    slug, n = base, 2
    while db.scalar(select(ApiConnection).where(ApiConnection.slug == slug)):
        slug, n = f"{base[:17]}_{n}", n + 1
    conn = ApiConnection(slug=slug, name=body.name.strip(), base_url=body.base_url)
    _apply(conn, body)
    db.add(conn)
    db.flush()
    _set_grants(db, conn, body.users)
    db.commit()
    audit.record(db, "connection.create", actor=me, target=conn.name, request=request, detail={"url": conn.base_url, "auth": conn.auth_type})
    return row(conn, db)


@router.put("/{conn_id}")
def update(conn_id: str, body: ConnectionIn, request: Request, me: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    conn = _get(db, conn_id)
    if db.scalar(select(ApiConnection).where(ApiConnection.name.ilike(body.name.strip()), ApiConnection.id != conn.id)):
        raise HTTPException(409, "Une connexion porte déjà ce nom.")
    before = row(conn, db)
    _apply(conn, body)
    _set_grants(db, conn, body.users)
    db.commit()
    after = row(conn, db)
    changed = {k: ("modifié" if k in ("secret",) else after[k]) for k in after if k not in ("updated_at", "operations") and after[k] != before[k]}
    if body.secret is not None and body.auth_type != "none":
        changed["secret"] = "modifié"
    if after["operations"] != before["operations"]:
        changed["opérations"] = f"{after['ops_enabled']} activée(s) sur {len(after['operations'])}"
    audit.record(db, "connection.update", actor=me, target=conn.name, request=request, detail=changed)
    return after


@router.delete("/{conn_id}", status_code=204)
def delete_connection(conn_id: str, request: Request, me: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    conn = _get(db, conn_id)
    name = conn.name
    db.delete(conn)
    db.commit()
    audit.record(db, "connection.delete", actor=me, target=name, request=request)


@router.post("/import")
async def import_spec(body: ImportIn, request: Request, me: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """Lit une spécification OpenAPI / Swagger et propose ses opérations de consultation (aucune n'est activée)."""
    conn = db.get(ApiConnection, body.connection_id) if body.connection_id else None
    try:
        result = await C.load_spec(body.url, body.text, conn)
    except ValueError as e:
        raise HTTPException(422, str(e))
    audit.record(db, "connection.import", actor=me, target=body.url or "(texte collé)", request=request,
                 detail={"operations": len(result["operations"]), "ignorées": result["skipped"]})
    return result


@router.post("/{conn_id}/test")
async def test(conn_id: str, request: Request, me: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    conn = _get(db, conn_id)
    result = await C.test_connection(conn)
    audit.record(db, "connection.test", actor=me, target=conn.name, request=request, detail={"ok": result["ok"], "status": result.get("status")})
    return result


@router.post("/{conn_id}/try")
async def try_operation(conn_id: str, body: TryIn, request: Request, me: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """Essai d'une opération (même désactivée) avec des valeurs saisies : montre ce que le modèle recevrait. Journalisé."""
    conn = _get(db, conn_id)
    out = await C.execute(db, me, f"{conn.slug}__{body.operation}", body.params, {}, request=request, force=True)
    return {"card": out.card, "result": out.text[:8000]}


@user_router.get("")
def my_connections(user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    """Connexions que l'utilisateur peut utiliser, et s'il les a activées pour lui."""
    off = set(settings.user_prefs(user.id).disabled_connections)
    return [{"id": c.id, "name": c.name, "description": c.description, "active": c.id not in off,
             "operations": [o.label or o.id for o in C.operations_of(c) if o.enabled]}
            for c in C.accessible(db, user.id)]
