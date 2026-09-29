"""
Configuration de l'application, lue depuis les variables d'environnement
(fichier .env chargé par systemd ou par le shell). Valeurs par défaut adaptées
au serveur actuel.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]          # /opt/assistant-opti
FRONTEND_DIR = BASE_DIR / "frontend"

# Ollama tourne sur le même serveur et n'écoute qu'en local.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.getenv("OPTI_MODEL", "qwen3:30b-a3b-instruct-2507-q4_K_M")
MODEL_LABEL = os.getenv("OPTI_MODEL_LABEL", "Opti · Qwen 3")

# Serveur web de l'application
HOST = os.getenv("OPTI_HOST", "0.0.0.0")
PORT = int(os.getenv("OPTI_PORT", "8100"))

# Consigne système envoyée en tête de chaque conversation
SYSTEM_PROMPT = os.getenv("OPTI_SYSTEM_PROMPT", (
    "Tu es l'Assistant Opti, l'assistant interne des collaborateurs d'Opti Sécurité, "
    "société française de télésurveillance et de sécurité. "
    "Tu réponds en français, de façon claire, structurée et professionnelle. "
    "Tu utilises le Markdown (titres courts, listes, tableaux) quand cela aide la lecture. "
    "Si tu n'es pas sûr d'une information, dis-le plutôt que d'inventer."
))
