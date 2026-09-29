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
- `backend/app/files.py`  : fichiers joints (extraction PDF/Word/texte, profil CSV/Excel)
- `backend/app/agent.py`  : contexte envoyé au modèle et boucle d'analyse de données
- `backend/app/sandbox.py`: exécution isolée du code d'analyse (conteneur Docker jetable)
- `deploy/sandbox/`       : image Docker du bac à sable
- `backend/app/documents.py` : documents longs : découpage, recherche de passages (BM25), synthèse section par section
- `backend/app/web.py`    : recherche web (SearXNG), lecture des pages, protection SSRF
- `backend/app/settings.py` : réglages globaux (administration) et préférences utilisateur
- `backend/app/admin.py` / `me.py` : API d'administration et de l'espace personnel
- `frontend/js/settings.js` : fenêtres Réglages et Administration
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
4. ✅ Pièces jointes : documents (PDF, Word, texte) et analyse de données (CSV, Excel) sur le fichier complet
5. ✅ Recherche web via SearXNG : automatique quand nécessaire (ou forcée), requêtes anonymisées, sources citées, protection SSRF
6. ✅ Réglages utilisateur (ton, longueur, instructions, texte, envoi, export) et Administration (comptes, modèle, consigne, options, statistiques)
7. ✅ Lecture avancée des PDF : tableaux, OCR des scans, longs documents (passages pertinents ou synthèse), pages citées
8. Bases documentaires (RAG avec embeddings)
9. Authentification LDAP (AD AMG.lan) en complément des comptes locaux, HTTPS

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

## Bac à sable d'analyse de données

Le code Python écrit par le modèle pour analyser un CSV/Excel s'exécute dans un conteneur jetable :
sans réseau, système de fichiers en lecture seule, utilisateur non privilégié, 2 Go de RAM, 2 CPU,
64 processus, 60 secondes maximum, fichiers de la conversation montés en lecture seule dans `/data`.

```bash
docker build -t opti-sandbox:2 deploy/sandbox
venv/bin/python backend/manage.py test-sandbox     # les 5 contrôles d'isolation, la lecture PDF et l'OCR doivent être ✓
```

## Lecture des PDF

Les PDF sont lus **dans le bac à sable** (jamais par l'application elle-même) : texte, tableaux à bordures
(convertis en Markdown) et OCR automatique des pages scannées (tesseract, français + anglais).

- **Document court** (moins de ~24 000 caractères) : donné en entier au modèle.
- **Document long, question précise** : passages les plus proches de la question (BM25) avec leurs pages ; le modèle cite les pages.
- **Document long, demande globale** (« résume », « analyse ce document ») : synthèse section par section, mise en cache
  dans `data/files/<id>/summary.json`.

Réglages (Administration → Recherche & fichiers) : OCR on/off, pages scannées lues par PDF.
Variables : `OPTI_MAX_PDF_PAGES` (300), `OPTI_PDF_TIMEOUT` (300 s).
