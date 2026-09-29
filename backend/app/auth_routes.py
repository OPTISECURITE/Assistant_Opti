"""Routes de connexion / déconnexion / identité."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from . import config
from .auth import (CurrentUser, authenticate, clear_failures, create_session, current_user,
                   delete_session, is_locked, record_failure)
from .db import get_db

router = APIRouter(prefix="/api", tags=["auth"])


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=512)


@router.post("/auth/login")
def login(body: Login, request: Request, response: Response, db: DbSession = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    keys = (f"u:{body.username.strip().lower()}", f"ip:{ip}")
    if is_locked(*keys):
        raise HTTPException(status_code=429, detail="Trop de tentatives. Réessayez dans quelques minutes.")
    user = authenticate(db, body.username, body.password)
    if not user:
        record_failure(*keys)
        raise HTTPException(status_code=401, detail="Identifiant ou mot de passe incorrect.")
    clear_failures(*keys)
    token = create_session(db, user)
    response.set_cookie(
        config.SESSION_COOKIE, token,
        max_age=config.SESSION_DAYS * 86400, httponly=True, samesite="lax",
        secure=config.COOKIE_SECURE, path="/",
    )
    return {"display_name": user.display_name}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, db: DbSession = Depends(get_db)):
    delete_session(db, request.cookies.get(config.SESSION_COOKIE))
    response.delete_cookie(config.SESSION_COOKIE, path="/")


@router.get("/me")
def me(user: CurrentUser = Depends(current_user)):
    return {"id": user.id, "username": user.username,
            "display_name": user.display_name, "is_admin": user.is_admin}
