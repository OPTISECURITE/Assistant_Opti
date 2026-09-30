"""
Chiffrement des secrets enregistrés (jetons, clés d'API, mots de passe des connexions).

Ils sont chiffrés dans la base (Fernet) avec une clé qui n'est PAS dans la base : `data/secret.key` (créée au premier usage,
lisible par root seulement) ou la variable OPTI_SECRET_KEY. Une copie de la base (sauvegarde, fichier copié) ne livre donc pas
les secrets. Ils ne sont jamais renvoyés par l'API : l'interface n'en connaît que la présence.
"""
import os

from cryptography.fernet import Fernet, InvalidToken

from . import config

_fernet: Fernet | None = None


def _key() -> bytes:
    env = os.getenv("OPTI_SECRET_KEY", "").strip()
    if env:
        return env.encode()
    path = config.DATA_DIR / "secret.key"
    if path.exists():
        return path.read_bytes().strip()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)      # jamais lisible par les autres utilisateurs du serveur
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def _f() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_key())
    return _fernet


def encrypt(text: str) -> str:
    return _f().encrypt(text.encode()).decode() if text else ""


def decrypt(token: str) -> str:
    """Le secret en clair, ou "" si absent ou illisible (clé changée)."""
    if not token:
        return ""
    try:
        return _f().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return ""
