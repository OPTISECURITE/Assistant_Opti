"""
Configuration de l'application, lue depuis les variables d'environnement
(fichier .env chargé par systemd ou par le shell). Valeurs par défaut adaptées
au serveur actuel.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]          # /opt/assistant-opti
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = Path(os.getenv("OPTI_DATA_DIR", BASE_DIR / "data"))
DATABASE_URL = os.getenv("OPTI_DATABASE_URL", f"sqlite:///{DATA_DIR / 'assistant.db'}")

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

# Sessions de connexion
SESSION_COOKIE = "opti_session"
# À passer à true dès que l'application est servie en HTTPS
COOKIE_SECURE = os.getenv("OPTI_COOKIE_SECURE", "false").lower() == "true"

# Fichiers joints
FILES_DIR = DATA_DIR / "files"
MAX_UPLOAD_MB = int(os.getenv("OPTI_MAX_UPLOAD_MB", "25"))
DOC_CHAR_BUDGET = int(os.getenv("OPTI_DOC_CHAR_BUDGET", "24000"))        # texte de documents injecté (~7k tokens)
HISTORY_CHAR_BUDGET = int(os.getenv("OPTI_HISTORY_CHAR_BUDGET", "20000"))  # historique conservé (~6k tokens)

# Exécution du code d'analyse de données
# docker     : conteneur jetable isolé (production)
# subprocess : processus local limité (développement uniquement, non isolé)
SANDBOX_MODE = os.getenv("OPTI_SANDBOX_MODE", "docker")
SANDBOX_IMAGE = os.getenv("OPTI_SANDBOX_IMAGE", "opti-sandbox:2")
SANDBOX_TIMEOUT = int(os.getenv("OPTI_SANDBOX_TIMEOUT", "60"))
SANDBOX_MEMORY = os.getenv("OPTI_SANDBOX_MEMORY", "2g")
MAX_ANALYSIS_STEPS = int(os.getenv("OPTI_MAX_ANALYSIS_STEPS", "3"))

# Recherche web (SearXNG installé sur le serveur)
SEARXNG_URL = os.getenv("OPTI_SEARXNG_URL", "http://127.0.0.1:8888")
WEB_RESULTS = int(os.getenv("OPTI_WEB_RESULTS", "5"))        # résultats donnés au modèle
WEB_PAGES_READ = int(os.getenv("OPTI_WEB_PAGES_READ", "3"))  # pages lues en entier parmi eux
WEB_PAGE_CHARS = int(os.getenv("OPTI_WEB_PAGE_CHARS", "3500"))

# Lecture des PDF
MAX_PDF_PAGES = int(os.getenv("OPTI_MAX_PDF_PAGES", "300"))   # pages lues au maximum par PDF
PDF_TIMEOUT = int(os.getenv("OPTI_PDF_TIMEOUT", "300"))       # durée maximale de lecture d'un PDF (OCR compris)

# Lecture complète des documents longs (synthèse / relevé exhaustif) : contexte plus large, historique réduit
DOC_FULL_BUDGET = int(os.getenv("OPTI_DOC_FULL_BUDGET", "28000"))
HISTORY_FULL_BUDGET = int(os.getenv("OPTI_HISTORY_FULL_BUDGET", "6000"))
MAX_SECTIONS = int(os.getenv("OPTI_MAX_SECTIONS", "120"))   # sections lues au maximum par document

# Ports du STT et du TTS de l'agent vocal : le nombre de connexions ouvertes dessus donne le nombre d'appels en cours
VOICE_PORTS = os.getenv("OPTI_VOICE_PORTS", "8080,8089")
VOICE_CONNS_PER_CALL = max(1, int(os.getenv("OPTI_VOICE_CONNS_PER_CALL", "1")))   # connexions ouvertes par appel sur chacun de ces ports
