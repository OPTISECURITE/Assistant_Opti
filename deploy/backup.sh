#!/usr/bin/env bash
# Sauvegarde Assistant Opti : base chiffrée (GPG) + code poussé sur GitHub.
set -e
cd /opt/assistant-opti

# 1. Copie cohérente de la base SQLite (compatible WAL), puis chiffrement
if [ -f data/assistant.db ]; then
    venv/bin/python - <<'PY'
import sqlite3
src = sqlite3.connect("data/assistant.db")
dst = sqlite3.connect("/tmp/assistant-backup.db")
src.backup(dst)
dst.close(); src.close()
PY
    gpg --batch --yes --passphrase-file /root/.backup_passphrase \
        -c --cipher-algo AES256 -o assistant-db.gpg /tmp/assistant-backup.db
    rm -f /tmp/assistant-backup.db
fi

# 2. Commit + push (le .env et data/ restent exclus par le .gitignore)
git add -A
git commit -m "Sauvegarde $(date '+%F %H:%M')" || true
git push || echo "[!] Push impossible"
echo "[✓] Sauvegarde Assistant Opti terminée."
