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

## Lecture des PDF et des documents longs

Les PDF sont lus **dans le bac à sable** (jamais par l'application elle-même) : texte, tableaux à bordures
(convertis en Markdown) et OCR automatique des pages scannées, des pages-images avec en-tête texte et des pages au
texte illisible (tesseract, français + anglais).

Un modèle ne peut pas lire 80 pages d'un coup (contexte de 16k tokens) : les documents longs sont lus par morceaux,
puis assemblés. Le mode est choisi selon la question (`backend/app/documents.py`) :

| Mode | Quand | Ce qui se passe |
|---|---|---|
| entier | document court (< ~24 000 caractères) | donné tel quel |
| extraits | question précise (une valeur, une clause) | passages les plus pertinents (BM25) avec leurs pages |
| synthèse | « résume », « explique ce document »… | **toutes** les sections sont lues (notes de 150 à 220 mots), fusionnées par niveaux si elles dépassent le contexte |
| exhaustif | « liste tous… », « relève les risques »… | **chaque** section est relue avec la question en tête, puis les relevés sont assemblés |

Le choix est fait par des mots-clés, puis par le modèle pour les formulations ambiguës. Le réglage utilisateur
« Toujours tout lire » (Réglages → Réponses) force la lecture complète à chaque question.
Les notes de la synthèse sont en cache dans `data/files/<id>/summary.json` (version 2).

**Couverture** : tout ce qui n'a pas pu être lu (pages scannées au-delà de la limite d'OCR, texte illisible, limite de
pages, section en échec) est signalé à l'utilisateur (pastille et encart d'alerte) et au modèle.

Réglages (Administration → Recherche & fichiers) : OCR on/off, pages scannées lues par PDF (100 par défaut).
Variables : `OPTI_MAX_PDF_PAGES` (300), `OPTI_PDF_TIMEOUT` (300 s), `OPTI_DOC_FULL_BUDGET` (28000), `OPTI_MAX_SECTIONS` (120).

## Sessions et déconnexion automatique

- **Inactivité** : après 30 minutes sans activité (réglable, Administration → Sécurité, 5 minutes minimum), l'utilisateur est
  déconnecté. Une fenêtre l'avertit 60 secondes avant ; seul un clic ou une touche la ferme. L'écran est vidé (messages,
  historique, brouillons, pièces jointes) et la page de connexion l'explique.
- **Durée maximale** : 12 heures (réglable), même pour un utilisateur actif.
- **Côté serveur** : l'échéance de la session glisse à chaque requête (une écriture par minute au plus) ; le navigateur envoie un
  signal de présence (`POST /api/auth/ping`) au plus une fois par minute quand l'écran est utilisé, ou pendant qu'une réponse se génère.
  Une session inactive est refusée par le serveur même si l'onglet est resté ouvert ou a été fermé puis rouvert.
- Les nouveaux délais s'appliquent immédiatement, y compris aux sessions déjà ouvertes.
