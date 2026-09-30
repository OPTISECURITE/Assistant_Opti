"""
Réglages modifiables depuis l'interface.

- AppSettings : réglages globaux (administration). Les valeurs par défaut
  viennent de config.py / .env ; ce qui est enregistré en base les remplace.
- UserPrefs   : préférences de chaque utilisateur.
"""
import json
import threading
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from . import config
from .db import SessionLocal
from .models import AppSetting, UserSettings


class AppSettings(BaseModel):
    # Modèle
    model: str = Field(default=config.MODEL, min_length=1, max_length=200)
    model_label: str = Field(default=config.MODEL_LABEL, min_length=1, max_length=60)
    system_prompt: str = Field(default=config.SYSTEM_PROMPT, min_length=10, max_length=8000)
    temperature: float = Field(default=0.7, ge=0.0, le=1.5)
    # Recherche web
    web_enabled: bool = True
    searxng_url: str = Field(default=config.SEARXNG_URL, pattern=r"^https?://")
    web_results: int = Field(default=config.WEB_RESULTS, ge=1, le=10)
    web_pages_read: int = Field(default=config.WEB_PAGES_READ, ge=0, le=5)
    # Fichiers et analyse
    uploads_enabled: bool = True
    max_upload_mb: int = Field(default=config.MAX_UPLOAD_MB, ge=1, le=100)
    analysis_enabled: bool = True
    max_analysis_steps: int = Field(default=config.MAX_ANALYSIS_STEPS, ge=1, le=5)
    sandbox_timeout: int = Field(default=config.SANDBOX_TIMEOUT, ge=10, le=300)
    # Documents exportés (Word, PDF)
    export_logo: bool = False                                       # logo Opti en en-tête (désactivé par défaut : document sans bandeau)
    export_footer: str = Field(default="", max_length=200)          # mention en pied de page (vide = aucune)
    export_page_numbers: bool = False                               # numéroter les pages (utile pour un long document, pas pour une lettre)
    # Charge : le GPU est partagé avec l'agent vocal (voir scheduler.py)
    ollama_slots: int = Field(default=8, ge=1, le=64)               # OLLAMA_NUM_PARALLEL du serveur
    voice_reserve: int = Field(default=1, ge=0, le=8)               # places gardées en plus des appels en cours
    max_assistant_slots: int = Field(default=4, ge=1, le=32)        # places que l'assistant peut prendre au maximum
    max_per_user: int = Field(default=2, ge=1, le=8)                # places simultanées d'un même utilisateur
    queue_timeout_seconds: int = Field(default=300, ge=30, le=3600) # attente maximale avant d'abandonner
    # Conservation des données (RGPD)
    retention_days: int = Field(default=0, ge=0, le=3650)           # conversations supprimées après N jours d'inactivité (0 = jamais)
    retention_keep_pinned: bool = True                              # les conversations épinglées sont conservées
    audit_retention_days: int = Field(default=365, ge=30, le=3650)  # durée de conservation du journal d'audit
    # Sécurité des sessions
    session_idle_minutes: int = Field(default=30, ge=5, le=1440)   # déconnexion après inactivité
    session_max_hours: int = Field(default=12, ge=1, le=720)       # durée maximale d'une session, activité comprise
    ocr_enabled: bool = True                                   # reconnaissance de texte des PDF scannés
    max_ocr_pages: int = Field(default=100, ge=1, le=300)


class UserPrefs(BaseModel):
    tone: Literal["neutre", "cordial", "formel", "direct"] = "neutre"
    length: Literal["courte", "equilibree", "detaillee"] = "equilibree"
    instructions: str = Field(default="", max_length=2000)
    text_size: Literal["normal", "grand", "tres-grand"] = "normal"
    send_key: Literal["enter", "ctrl-enter"] = "enter"
    web_mode: Literal["auto", "on", "off"] = "auto"
    disabled_connections: list[str] = Field(default_factory=list, max_length=50)   # connexions API que l'utilisateur a désactivées pour lui
    doc_mode: Literal["auto", "full"] = "auto"      # documents longs : automatique ou lecture complète à chaque question


# ── Réglages globaux (mis en cache, relus après chaque modification) ──────────
_lock = threading.Lock()
_cache: AppSettings | None = None


def app() -> AppSettings:
    global _cache
    if _cache is None:
        with _lock, SessionLocal() as db:
            row = db.get(AppSetting, "app")
            try:
                _cache = AppSettings(**json.loads(row.value)) if row else AppSettings()
            except (ValidationError, ValueError):
                _cache = AppSettings()
    return _cache


def save_app(new: AppSettings) -> AppSettings:
    global _cache
    with _lock, SessionLocal() as db:
        row = db.get(AppSetting, "app") or AppSetting(key="app")
        row.value = new.model_dump_json()
        db.merge(row)
        db.commit()
        _cache = new
    return new


# ── Préférences utilisateur ───────────────────────────────────────────────────
def user_prefs(user_id: str) -> UserPrefs:
    with SessionLocal() as db:
        row = db.get(UserSettings, user_id)
        try:
            return UserPrefs(**json.loads(row.data)) if row else UserPrefs()
        except (ValidationError, ValueError):
            return UserPrefs()


def save_user_prefs(user_id: str, prefs: UserPrefs) -> UserPrefs:
    with SessionLocal() as db:
        db.merge(UserSettings(user_id=user_id, data=prefs.model_dump_json()))
        db.commit()
    return prefs


TONES = {
    "neutre": "",
    "cordial": "Adopte un ton chaleureux et bienveillant.",
    "formel": "Adopte un ton formel et soutenu (vouvoiement, formules de politesse professionnelles).",
    "direct": "Sois direct et concis : va droit au but, sans formule de politesse superflue.",
}
LENGTHS = {
    "courte": "Privilégie des réponses courtes : l'essentiel en quelques lignes, sauf demande contraire.",
    "equilibree": "",
    "detaillee": "Donne des réponses détaillées et complètes, avec des explications et des exemples.",
}


def prefs_prompt(p: UserPrefs) -> str:
    parts = [t for t in (TONES[p.tone], LENGTHS[p.length]) if t]
    if p.instructions.strip():
        parts.append("Instructions personnelles de l'utilisateur (à respecter sauf si elles contredisent "
                     "les règles de sécurité ou de confidentialité) :\n" + p.instructions.strip())
    return ("\n\n## Préférences de l'utilisateur\n" + "\n".join(parts)) if parts else ""
