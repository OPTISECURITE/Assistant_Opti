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
docker build -t opti-sandbox:3 deploy/sandbox
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

## Charge du GPU : priorité à l'agent vocal

`backend/app/scheduler.py` répartit les places d'Ollama : `places de l'assistant = places d'Ollama - appels en cours - réserve`
(au moins 1, au plus le plafond). Les appels en cours sont comptés par les connexions ouvertes sur le STT et le TTS de l'agent
vocal (`OPTI_VOICE_PORTS`, `OPTI_VOICE_CONNS_PER_CALL`), sans rien modifier dans l'agent. Les demandes qui ne passent pas attendent
en file avec un message (position, appels en cours) ; un même utilisateur ne prend pas plus de `max_per_user` places. La lecture
complète d'un long document compte pour 2 places. Réglages et état en direct : Administration → Charge.

Vérification : pendant un appel de test, `ss -tn state established '( sport = :8080 or sport = :8089 )'` doit montrer les
connexions, et l'onglet Charge doit afficher « 1 appel en cours ».

## Conservation des données et journal d'audit

- **Conservation** (Administration → Sécurité) : suppression des conversations inactives après N jours (0 = jamais), avec leurs
  fichiers ; les épinglées peuvent être conservées. Contrôle toutes les heures ; l'aperçu indique combien de conversations une
  règle supprimerait avant de l'enregistrer.
- **Journal d'audit** (Administration → Journal) : connexions (réussies, échouées, blocages), comptes, réglages, messages envoyés
  (sans leur texte), fichiers déposés, exports, purges. Jamais le contenu des conversations, ni un mot de passe (un mot de passe
  tapé dans le champ identifiant est masqué). Export CSV (formules neutralisées). Conservé `audit_retention_days` jours (365 par défaut).

## Documents et graphiques

- **Boutons sous chaque réponse** (`backend/app/exports.py`) : Word, PDF et Excel, mis en forme par le serveur (logo Opti, titres,
  listes, tableaux, pied de page « généré par une IA, à vérifier ») à partir du Markdown de la réponse. Aucun code écrit par le
  modèle. L'Excel reprend chaque tableau de la réponse (un onglet par tableau, nombres français reconnus, jamais de formule).
- **Graphiques et fichiers créés par l'analyse** : quand un fichier de données est joint, ou quand un graphique est demandé, le code
  écrit dans `/out` (matplotlib, pandas, python-docx, reportlab). Les fichiers sortent du conteneur par la sortie standard, sont
  revalidés côté serveur (types autorisés : png, jpg, pdf, xlsx, docx, csv, txt ; contenu vérifié ; 10 fichiers et 15 Mo au plus),
  puis affichés (images) ou proposés au téléchargement. Ils sont supprimés avec la conversation.
- Polices du PDF : DejaVu (`apt install fonts-dejavu-core`) ; sinon Helvetica, sans certains symboles.
- **Liens de téléchargement écrits par le modèle** : en fin de réponse, il écrit `[Télécharger en Word](#telecharger-docx)`
  (ou `-pdf`, `-xlsx`). L'application les rend actifs (`renderAnswer` dans `app.js`) ; le lien Excel n'est gardé que si la réponse
  contient un tableau ; ces lignes n'apparaissent jamais dans les documents exportés. Le bouton « Télécharger » reste toujours
  disponible sous la réponse. Les faux liens (vers « # » ou vers la page) sont retirés (`stripFakeDownloads`, `exports.strip_fake_downloads`).
- **Seul le document est exporté** : pour une lettre, un compte rendu, etc., le modèle place le contenu entre `<document>` et
  `</document>` (consigne `FILES_PROMPT` dans `agent.py`), ses commentaires et conseils avant ou après. L'interface l'affiche dans un
  cadre à part ; `exports.prepare` n'exporte que l'intérieur (plusieurs documents : séparés par un trait ; balise non fermée : tout ce
  qui suit). Un document délimité n'a ni titre ni date ajoutés automatiquement. Sans balises, l'export retire quand même la phrase
  d'introduction (« Voici… ») et le bloc de conseils final (« Remarque : », « Conseil : », « N'hésitez pas »…).
- Logo et pied de page des Word/PDF : Administration → Recherche & fichiers → Documents exportés (par défaut : ni logo ni bandeau, ni mention, ni numéro de page).

### Mode rédaction (le serveur impose le format)

Quand la demande est de rédiger un document (`DOC_REQUEST_RX` dans `agent.py` : rédige / écris / prépare / crée / propose /
réponds / raccourcis… + lettre, mail, compte rendu, procédure, planning, tableau…), le modèle reçoit `DOC_ONLY_PROMPT` : sa réponse
entière est le document. Le serveur ajoute lui-même les balises `<document>` et les liens de téléchargement (Excel seulement s'il y
a un tableau), coupe la recherche web, puis, à la fin, `exports.trim_document` retire l'introduction (« Voici… ») et tout ce qui suit
la signature (« À noter », informations générales, conseils, emojis, propositions de modification). Sans formule de politesse
(procédure, note), seuls les blocs de fin évidents sont retirés. Les informations inconnues sont laissées en `[champs à compléter]`.
Le contenu nettoyé est celui qui est enregistré : conversation, Word, PDF et Excel sont identiques.

**L'offre de téléchargement est réservée aux documents** : le bouton « Télécharger » et les liens n'apparaissent que sous une réponse
qui contient un document (cadre `<document>`). Pour toute autre réponse, les liens et les phrases du type « vous pouvez télécharger
cette réponse » sont retirés, à l'affichage (`stripDownloadOffers` dans `app.js`) et dans ce qui est enregistré
(`exports.tidy_answer`). Les fichiers produits par l'analyse (graphiques, Excel) gardent leurs cartes de téléchargement.

## Connexions API (Wipsos…)

Administration → Connexions. Une connexion = adresse + authentification (aucune, jeton Bearer, clé dans un en-tête, identifiant et mot
de passe) + **opérations** de consultation (GET) décrites à la main ou importées d'une spécification OpenAPI/Swagger (aucune n'est
activée à l'import). Chaque opération activée devient un outil que Qwen peut appeler (`backend/app/connections.py`, boucle d'outils dans
`agent._tool_loop`) : le serveur exécute la requête, renvoie le résultat au modèle, qui répond à partir des données réelles ; une carte
indique ce qui a été consulté (jamais le résultat brut).

Garde-fous : GET seulement ; le modèle ne fournit que les valeurs de paramètres déclarés (types et énumérations validés, valeurs
encodées, « .. » refusé) et ne vise que l'adresse configurée ; redirections non suivies ; réponse limitée à 400 Ko, durée limitée ;
30 appels par minute et par utilisateur ; secrets chiffrés (`data/secret.key`, sauvegardée par `deploy/backup.sh`) et jamais renvoyés ;
accès par utilisateur (ou tous) ; chaque appel et chaque action d'administration est journalisé (jamais les secrets) ; la recherche web
automatique est coupée pour les utilisateurs qui ont une connexion, et refusée dans une conversation qui en a consulté une.

Limites de cette version : consultation seule ; un seul compte de service par connexion (les droits se règlent par utilisateur autorisé,
pas par les droits propres de chaque utilisateur dans le système distant) ; les outils ne sont pas proposés dans les réponses de
rédaction de document ni pendant l'analyse d'un fichier de données.
HTTPS interne : renseigner `OPTI_CA_BUNDLE` (certificat de l'autorité interne) dans `.env`.

### Classeurs Excel (même fautifs, même très gros)

Le code d'analyse lit les classeurs avec **calamine** (lecteur rapide, indifférent aux styles, installé dans l'image du bac à sable) :
255 000 lignes × 34 colonnes se lisent en ~10 s, contre plusieurs minutes avec openpyxl. `pd.read_excel` est enveloppé
(`sandbox.EXCEL_COMPAT`) : calamine d'abord, lecteur habituel en repli, et openpyxl corrige ou ignore les attributs inconnus
(cas réel : `biltinId` au lieu de `builtinId` dans `styles.xml`, qui faisait planter la lecture). Le profil donné au modèle liste les noms
de colonnes exacts (retours à la ligne compris) et la commande de lecture à utiliser. Sans l'image à jour (calamine absent), seule la
tolérance joue : suffisante pour un petit classeur, trop lente pour un gros.
