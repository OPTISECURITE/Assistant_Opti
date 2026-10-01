#!/usr/bin/env bash
# Sauvegarde de l'Assistant Opti : tout est chiffré (GPG, AES-256) puis poussé avec le code sur GitHub.
#   - assistant-db.gpg  : la base (comptes, conversations, connexions API, journal d'audit)
#   - secret-key.gpg    : la clé qui chiffre les secrets des connexions API
#   - env.gpg           : le fichier .env (réglages du serveur)
# NON sauvegardés : les fichiers déposés ou produits (data/files), les images Docker (reconstruisibles), les modèles Ollama.
# Variables facultatives : OPTI_DIR, BACKUP_PASSPHRASE_FILE, OPTI_PYTHON.
set -euo pipefail
umask 077                                    # aucun fichier temporaire lisible par les autres comptes du serveur

APP_DIR="${OPTI_DIR:-/opt/assistant-opti}"
PASSFILE="${BACKUP_PASSPHRASE_FILE:-/root/.backup_passphrase}"
PYTHON="${OPTI_PYTHON:-venv/bin/python}"
cd "$APP_DIR"

[ -s "$PASSFILE" ] || { echo "[!] Phrase secrète de chiffrement introuvable : $PASSFILE" >&2; exit 1; }
encrypt() { gpg --batch --yes --quiet --passphrase-file "$PASSFILE" -c --cipher-algo AES256 -o "$2" "$1"; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# Le chiffrement GPG donne un fichier différent à chaque fois : sans précaution, chaque nuit ajouterait une copie de plus à l'historique
# du dépôt, même sans aucun changement. On ne rechiffre donc que si le contenu a changé depuis la dernière sauvegarde.
STATE="data/.backup-state"; mkdir -p "$STATE"
unchanged() { [ "$(cat "$STATE/$2" 2>/dev/null)" = "$1" ]; }         # $1 = empreinte, $2 = nom
remember()  { printf '%s' "$1" > "$STATE/$2"; }

# 1. Base SQLite : copie cohérente (compatible WAL), puis chiffrement
if [ -f data/assistant.db ]; then
    HASH="$("$PYTHON" - "$TMP/assistant.db" <<'PY'
import hashlib, sqlite3, sys
src = sqlite3.connect("data/assistant.db")
dst = sqlite3.connect(sys.argv[1])
src.backup(dst)
src.close()
h = hashlib.sha256()
for line in dst.iterdump():                  # empreinte du CONTENU (stable, contrairement au fichier)
    h.update(line.encode() + b"\n")
dst.close()
print(h.hexdigest())
PY
)"
    if unchanged "$HASH" db && [ -f assistant-db.gpg ]; then echo "[=] Base inchangée"; else encrypt "$TMP/assistant.db" assistant-db.gpg; remember "$HASH" db; echo "[✓] Base chiffrée"; fi
fi

# 2. Clé de chiffrement des secrets des connexions API (sans elle, ces secrets seraient à ressaisir après une restauration)
if [ -f data/secret.key ]; then
    H="$(sha256sum data/secret.key | cut -d' ' -f1)"
    if unchanged "$H" key && [ -f secret-key.gpg ]; then echo "[=] Clé des secrets inchangée"; else encrypt data/secret.key secret-key.gpg; remember "$H" key; echo "[✓] Clé des secrets chiffrée"; fi
fi

# 3. Réglages du serveur
if [ -f .env ]; then
    H="$(sha256sum .env | cut -d' ' -f1)"
    if unchanged "$H" env && [ -f env.gpg ]; then echo "[=] .env inchangé"; else encrypt .env env.gpg; remember "$H" env; echo "[✓] .env chiffré"; fi
fi

# 4. Commit + envoi (le .env, data/ et les clés en clair restent exclus par le .gitignore)
git add -A
git commit -m "Sauvegarde $(date '+%F %H:%M')" >/dev/null || echo "[i] Rien de nouveau à enregistrer."
if git push --quiet; then
    echo "[✓] Sauvegarde envoyée sur GitHub ($(git rev-parse --short HEAD))."
else
    echo "[!] ÉCHEC : la sauvegarde est faite en local mais N'A PAS été envoyée sur GitHub." >&2
    exit 1
fi
