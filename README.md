# Assistant Opti

Interface web interne d'Opti Sécurité, branchée sur le modèle Qwen servi par Ollama.
100 % local : aucune ressource externe n'est chargée (polices, icônes et librairies servies par l'application).

## Architecture

```
navigateur ──HTTP──> backend FastAPI (port 8100) ──> Ollama (127.0.0.1:11434) ──> Qwen
                      └─ sert aussi le front (frontend/)
```

- `backend/app/config.py` : configuration (variables d'environnement, voir `.env.example`)
- `backend/app/ollama.py` : client Ollama en streaming, gestion des erreurs
- `backend/app/main.py`   : point d'entrée, service du front
- `backend/app/chats.py`  : API des conversations (streaming enregistré en base)
- `backend/app/models.py` / `db.py` : tables et connexion (SQLite, `data/assistant.db`)
- `backend/app/auth.py`   : comptes (Argon2id), sessions par cookie HttpOnly, anti-force brute
- `backend/app/auth_routes.py` : connexion / déconnexion / identité
- `backend/manage.py`     : administration des comptes en ligne de commande
- `frontend/` : interface issue de la maquette Opti (`css/opti.css` = CSS de la maquette, `css/app.css` = compléments)

## Installation

```bash
cd /opt/assistant-opti
python3 -m venv venv
venv/bin/pip install -r backend/requirements.txt
cp .env.example .env
cp deploy/assistant-opti.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now assistant-opti
```

Interface : `http://<serveur>:8100`

## Feuille de route

1. ✅ Chat en streaming, design de la maquette, thème clair/sombre/système
2. ✅ Persistance des conversations (renommer, épingler, télécharger, supprimer, recherche)
3. ✅ Connexion par comptes locaux, « Bonjour [prénom] », menu profil
5. Authentification LDAP (AD AMG.lan) en complément des comptes locaux, HTTPS
4. Documents (RAG) et analyse de fichiers côté serveur

## Sauvegarde

`deploy/backup.sh` chiffre la base (même passphrase que les autres sauvegardes) puis pousse le dépôt.
À planifier : `0 2 * * * /opt/assistant-opti/deploy/backup.sh >> /var/log/assistant-opti-backup.log 2>&1`

## Comptes utilisateurs

```bash
cd /opt/assistant-opti
venv/bin/python backend/manage.py create-user m.chaput "Maxime Chaput" --admin
venv/bin/python backend/manage.py list-users
venv/bin/python backend/manage.py set-password m.chaput
venv/bin/python backend/manage.py disable-user j.dupont
```
