"""Routes de connexion / déconnexion / identité."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

import re

from . import audit, config, settings
from .auth import (CurrentUser, authenticate, clear_failures, create_session, current_user,
                   delete_session, is_locked, record_failure, user_of_token)
from .db import get_db

router = APIRouter(prefix="/api", tags=["auth"])


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=512)


@router.post("/auth/login")
def login(body: Login, request: Request, response: Response, db: DbSession = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    keys = (f"u:{body.username.strip().lower()}", f"ip:{ip}")
    # Un mot de passe tapé par erreur dans le champ identifiant ne doit jamais atterrir dans le journal
    tried = body.username.strip()[:40] if re.fullmatch(r"[A-Za-z0-9._-]{1,40}", body.username.strip()) else "(identifiant invalide)"
    if is_locked(*keys):
        audit.record(db, "auth.locked", actor_name=tried, request=request)
        raise HTTPException(status_code=429, detail="Trop de tentatives. Réessayez dans quelques minutes.")
    user = authenticate(db, body.username, body.password)
    if not user:
        record_failure(*keys)
        audit.record(db, "auth.login_failed", actor_name=tried, request=request)
        raise HTTPException(status_code=401, detail="Identifiant ou mot de passe incorrect.")
    clear_failures(*keys)
    token = create_session(db, user)
    audit.record(db, "auth.login", actor=user, request=request)
    response.set_cookie(
        config.SESSION_COOKIE, token,
        max_age=settings.app().session_max_hours * 3600, httponly=True, samesite="lax",
        secure=config.COOKIE_SECURE, path="/",
    )
    return {"display_name": user.display_name}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, db: DbSession = Depends(get_db)):
    token = request.cookies.get(config.SESSION_COOKIE)
    who = user_of_token(db, token)
    if who:
        audit.record(db, "auth.logout", actor=who, request=request)
    delete_session(db, token)
    response.delete_cookie(config.SESSION_COOKIE, path="/")


@router.post("/auth/ping", status_code=204)
def ping(user: CurrentUser = Depends(current_user)):
    """Signal de présence : l'écran est utilisé, la session reste ouverte (la dépendance prolonge l'échéance)."""


@router.get("/me")
def me(user: CurrentUser = Depends(current_user)):
    return {"id": user.id, "username": user.username,
            "display_name": user.display_name, "is_admin": user.is_admin}
