"""
Authentification : comptes locaux (mot de passe haché Argon2) et sessions
par cookie HttpOnly. Le LDAP s'ajoutera plus tard dans authenticate() sans
changer le reste de l'application.
"""
import hashlib
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from . import config, settings
from .db import get_db
from .models import Session, User, now_ms

_hasher = PasswordHasher()
# Hash factice : vérifié quand l'identifiant n'existe pas, pour que le temps de
# réponse ne révèle pas si un compte existe.
_DUMMY_HASH = _hasher.hash("mot-de-passe-factice")


@dataclass
class CurrentUser:
    id: str
    username: str
    display_name: str
    is_admin: bool


# ── Mots de passe ────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, InvalidHashError):
        return False


def authenticate(db: DbSession, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username.strip().lower()))
    if user is None:
        verify_password(None, password)   # même coût de calcul qu'un vrai compte
        return None
    if not user.active:
        return None
    if user.source == "local" and verify_password(user.password_hash, password):
        if _hasher.check_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        return user
    # source == "ldap" : branché à l'étape LDAP
    return None


# ── Anti-force brute : 5 échecs → blocage 5 minutes (par identifiant et par IP) ──
_FAILS: dict[str, list[float]] = defaultdict(list)
MAX_FAILS, WINDOW = 5, 300


def is_locked(*keys: str) -> bool:
    now = time.time()
    for k in keys:
        _FAILS[k] = [t for t in _FAILS[k] if now - t < WINDOW]
        if len(_FAILS[k]) >= MAX_FAILS:
            return True
    return False


def record_failure(*keys: str) -> None:
    for k in keys:
        _FAILS[k].append(time.time())


def clear_failures(*keys: str) -> None:
    for k in keys:
        _FAILS.pop(k, None)


# ── Sessions ─────────────────────────────────────────────────────────────────
def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: DbSession, user: User) -> str:
    token = secrets.token_urlsafe(32)
    now = now_ms()
    db.execute(delete(Session).where(Session.expires_at < now))   # ménage des sessions expirées
    s = settings.app()
    db.add(Session(token_hash=_digest(token), user_id=user.id, created_at=now,
                   expires_at=now + s.session_idle_minutes * 60_000))
    user.last_login_at = now
    db.commit()
    return token


def user_of_token(db: DbSession, token: str | None) -> User | None:
    sess = db.get(Session, _digest(token)) if token else None
    return db.get(User, sess.user_id) if sess else None


def delete_session(db: DbSession, token: str | None) -> None:
    if token:
        db.execute(delete(Session).where(Session.token_hash == _digest(token)))
        db.commit()


def current_user(request: Request, db: DbSession = Depends(get_db)) -> CurrentUser:
    token = request.cookies.get(config.SESSION_COOKIE)
    if token:
        sess = db.get(Session, _digest(token))
        if sess:
            now = now_ms()
            s = settings.app()
            hard_limit = sess.created_at + s.session_max_hours * 3_600_000       # durée maximale, activité comprise
            if sess.expires_at <= now or hard_limit <= now:
                db.delete(sess)                                                 # inactive trop longtemps, ou trop ancienne
                db.commit()
                raise HTTPException(status_code=401, detail="Session expirée")
            user = db.get(User, sess.user_id)
            if user and user.active:
                # Inactivité : l'échéance glisse à chaque requête (une écriture par minute au plus).
                # Elle est aussi ramenée à la limite si l'administrateur a raccourci les délais.
                deadline = min(now + s.session_idle_minutes * 60_000, hard_limit)
                if deadline - sess.expires_at > 60_000 or deadline < sess.expires_at:
                    sess.expires_at = deadline
                    db.commit()
                return CurrentUser(user.id, user.username, user.display_name, user.is_admin)
    raise HTTPException(status_code=401, detail="Authentification requise")
