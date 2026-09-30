"""
Construction du contexte envoyé au modèle et boucle d'analyse de données.

1. Contexte : consigne système + documents joints (texte extrait) + profil
   des fichiers de données + historique, dans un budget de caractères.
2. Boucle : si le modèle écrit un bloc ```python, on l'arrête dès que le bloc
   est fermé (pour qu'il n'invente pas de résultat), on exécute le code dans
   le bac à sable, on affiche la sortie puis on lui redonne la main. Au plus
   MAX_ANALYSIS_STEPS exécutions.
"""
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import AsyncIterator

from . import config, connections, documents, exports, ollama, sandbox, settings, voice, web
from .db import SessionLocal
from .models import User
from .scheduler import scheduler
from .files import file_path
from .models import Chat, File

log = logging.getLogger("opti.agent")

CODE_BLOCK = re.compile(r"```python[^\n]*\n(.*?)```", re.S)
SEARCH_BLOCK = re.compile(r"```(?:recherche|lecture|attente|fichiers|outil)\n.*?```\n*", re.S)   # encarts d'affichage, retirés de l'historique

ANALYSIS_PROMPT = """
## Analyse de fichiers de données
Des fichiers de données sont disponibles en lecture seule dans /data/ (voir leurs profils ci-dessous).
Tu ne vois qu'un aperçu : pour tout chiffre, comptage, statistique, filtre ou extraction, tu DOIS calculer avec du code.
- Écris un seul bloc ```python``` complet, qui lit le fichier entier avec pandas et affiche les résultats avec print().
- Arrête-toi juste après le bloc : le code sera exécuté et tu recevras sa sortie.
- Ensuite, rédige ta réponse en français en t'appuyant UNIQUEMENT sur les résultats obtenus. N'invente jamais un chiffre.
- En cas d'erreur, corrige le code et réessaie.
- Lis chaque fichier EXACTEMENT avec la commande indiquée dans son profil (« Pour lire ce fichier »).
"""


OUTPUT_HELP = """
### Graphiques et fichiers
- Le dossier /out est le seul où tu peux écrire. Tout fichier créé dedans (.png, .jpg, .xlsx, .docx, .pdf, .csv, .txt) est présenté à l'utilisateur, qui peut le télécharger.
- Graphique : matplotlib (import matplotlib ; matplotlib.use("Agg") ; import matplotlib.pyplot as plt), puis plt.savefig("/out/nom.png", dpi=120, bbox_inches="tight"). Titre, axes et légendes en français, lisibles ; une figure par fichier.
- Excel : df.to_excel("/out/nom.xlsx", index=False). Word : bibliothèque docx (python-docx). PDF : reportlab.
- Ne recopie pas le contenu des fichiers dans ta réponse : présente-les en une phrase.
"""

CHART_PROMPT = """
## Graphique
L'utilisateur demande un graphique. Écris UN bloc ```python``` qui le crée avec matplotlib à partir des valeurs qu'il t'a données (n'invente aucune valeur : s'il en manque, demande-les) et arrête-toi juste après le bloc : le code sera exécuté.
""" + OUTPUT_HELP

FILES_PROMPT = """
## Documents à rédiger et à télécharger
Tu ne peux ni créer, ni joindre, ni envoyer de fichier.
Quand l'utilisateur te demande de RÉDIGER un document (lettre, courrier, e-mail, compte rendu, note, procédure, tableau…) :
1. Écris le document lui-même entre les balises <document> et </document>, en Markdown (titre # si utile, sections ##, listes, tableaux). Entre ces balises, UNIQUEMENT le contenu du document, prêt à être envoyé tel quel : pas de « Voici… », pas de conseil, pas de remarque, pas de question, pas de lien.
2. Tes commentaires, conseils ou questions éventuels vont AVANT ou APRÈS les balises, en quelques lignes seulement.
3. Termine par UNE ligne de liens, écrits exactement ainsi (ce sont les seuls liens de téléchargement qui fonctionnent) : [Télécharger en Word](#telecharger-docx) · [Télécharger en PDF](#telecharger-pdf) ; et seulement si le document contient un tableau, ajoute : · [Télécharger en Excel](#telecharger-xlsx)
Ce qui est entre les balises est exactement ce que l'utilisateur téléchargera. N'invente jamais d'autre lien ni bouton de téléchargement.
Pour une simple question, une explication, une liste ou une réponse quelconque, réponds normalement : aucune balise, aucun lien, et ne propose JAMAIS de télécharger quoi que ce soit (pas de phrase du type « vous pouvez télécharger cette réponse »). Les balises et les liens ne servent que lorsque tu as rédigé un document à envoyer.

Exemple :
Voici une proposition, adaptée à un client professionnel.

<document>
Objet : Relance de facture n° 2026-0142

Madame, Monsieur,

Sauf erreur de notre part, la facture n° 2026-0142 reste impayée.

Cordialement,
Le service comptabilité
</document>

Vous pouvez l'envoyer en recommandé si la facture a plus de 60 jours.

[Télécharger en Word](#telecharger-docx) · [Télécharger en PDF](#telecharger-pdf)
"""

# Demande de rédaction d'un document (lettre, mail, compte rendu…) : le serveur impose alors le format (voir _generate)
DOC_REQUEST_RX = re.compile(
    r"\b(?:r[ée]dig|[ée]cri[srtv]|[ée]crir|g[ée]n[èe]r|pr[ée]par|cr[ée][ée]|cr[ée]er|produi|compos|propos|fais|faire|refai|r[ée]pond|"
    r"reformul|corrig|raccourc|adapt|r[ée][ée]cri|am[ée]lior|compl[èe]t|modifi|allong)\w*"
    r"[^.\n?!]{0,70}?\b(?:lettre|courrier|mail|e-?mail|courriel|message|compte[- ]?rendu|note|proc[ée]dure|attestation|contrat|convention|"
    r"avenant|devis|rapport|fiche|consigne|m[ée]mo|communiqu[ée]|r[ée]clamation|relance|mise en demeure|planning|tableau|texte|document|offre)\b",
    re.I)

DOC_ONLY_PROMPT = """
## Mode rédaction de document
L'utilisateur te demande un document. Ta réponse COMPLÈTE est le document lui-même, rien d'autre : elle sera placée telle quelle dans un fichier Word ou PDF envoyé à un tiers.
- Commence directement par le document : la première ligne est celle du courrier (« Objet : … ») ou le titre. Aucune phrase d'introduction (« Voici… », « Bien sûr… »).
- Termine par la signature ou la dernière ligne du document. Rien après : aucune conclusion, remarque, conseil, « à noter », explication, proposition de modification, question, emoji ni lien.
- Pour une information que tu ne connais pas (nom, date, montant, référence…), écris un champ à compléter entre crochets, par exemple [Nom du client] : n'invente rien et ne pose pas de question.
- Markdown : un titre # seulement si le document en a un ; listes et tableaux si utile.
"""

TOOLS_PROMPT = """
## Outils : systèmes internes de l'entreprise
Tu peux consulter en lecture seule des systèmes internes (clients, factures, tarifs…) avec les outils fournis.
- Pour toute question qui porte sur des données de ces systèmes, appelle l'outil adapté au lieu de répondre de mémoire : n'invente jamais une valeur.
- Si l'outil renvoie une erreur ou aucun résultat, dis-le simplement à l'utilisateur. Si plusieurs résultats correspondent (homonymes), demande-lui de préciser.
- Réponds uniquement à partir des résultats reçus et cite la source (le nom du système). Ces données sont confidentielles : ne les utilise que pour répondre à la question posée.
- Tu ne peux rien modifier dans ces systèmes.
"""

MAX_TOOL_ROUNDS = 4          # allers-retours modèle ↔ outils au plus par réponse
MAX_CALLS_PER_ROUND = 4

CHART_RX = re.compile(r"\b(graphique|courbe|histogramme|diagramme|camembert|nuage de points|barres? (?:empil|group))", re.I)


DOC_PROMPT = """
## Documents joints par l'utilisateur
Appuie-toi sur ces documents pour répondre. Quand tu reprends une information, indique la page concernée entre
parenthèses, par exemple (p. 12), uniquement si ce numéro figure dans les extraits fournis. N'invente rien : si le
document ne contient pas la réponse, dis-le.
"""


def build_messages(chat: Chat, files: list[File], prefs: settings.UserPrefs | None = None,
                   doc_context: str = "", history_budget: int | None = None, code_mode: str | None = None,
                   doc_request: bool = False, has_tools: bool = False) -> list[dict]:
    history_budget = history_budget or config.HISTORY_CHAR_BUDGET
    app = settings.app()
    data = [f for f in files if f.kind == "data"] if app.analysis_enabled else []

    system = app.system_prompt + (settings.prefs_prompt(prefs) if prefs else "")
    if doc_context:
        system += DOC_PROMPT + doc_context
    if data:
        system += ANALYSIS_PROMPT + OUTPUT_HELP
        for f in data:
            system += f"\n### Fichier de données « {f.filename} » → /data/{f.stored_name}\n{f.text[:4000]}\n"
    elif code_mode == "chart":
        system += CHART_PROMPT
    if has_tools:
        system += TOOLS_PROMPT
    if doc_request:
        system += DOC_ONLY_PROMPT
    elif code_mode is None:
        system += FILES_PROMPT

    # Historique : on garde les messages les plus récents dans le budget
    history, used = [], 0
    for m in reversed(chat.messages):
        if not m.content:
            continue
        if used + len(m.content) > history_budget and history:
            break
        content = m.content
        if m.role == "assistant":                       # ni encarts d'affichage, ni balises <document>, ni liens de téléchargement
            content = re.sub(r"</?document[^>]*>", "", exports.strip_fake_downloads(SEARCH_BLOCK.sub("", content)), flags=re.I)
        history.append({"role": m.role, "content": content})
        used += len(m.content)
    history.reverse()
    if history and history[0]["role"] == "assistant":
        history.pop(0)
    return [{"role": "system", "content": system}, *history]


@dataclass
class Plan:
    """Ce qu'il faut faire des documents joints pour cette question (décidé avant de réserver une place)."""
    question: str = ""
    search_query: str = ""
    mode: str = documents.EXTRAITS
    has_docs: bool = False

    @property
    def heavy(self) -> bool:
        return self.has_docs and self.mode != documents.EXTRAITS


async def _plan(chat: Chat, docs: list[File], prefs: settings.UserPrefs) -> Plan:
    user_msgs = [m.content for m in chat.messages if m.role == "user"]
    question = user_msgs[-1] if user_msgs else ""
    if not docs:
        return Plan(question=question)
    search_query = question if len(question) >= 30 or len(user_msgs) < 2 else user_msgs[-2] + " " + question
    light_limit = max(3000, config.DOC_CHAR_BUDGET // len(docs))
    mode = documents.EXTRAITS
    if any(len(f.text) > light_limit for f in docs):              # au moins un document trop long pour tenir en entier
        mode = await documents.decide_mode(question, documents.conversation_excerpt(chat.messages), prefs.doc_mode)
    return Plan(question, search_query, mode, True)


async def run(chat: Chat, files: list[File], web_mode: str = "auto", sink=None) -> AsyncIterator[str]:
    """
    web_mode : 'auto' (le modèle décide), 'on' (recherche forcée), 'off' (jamais).
    La génération attend d'avoir une place sur le GPU (voir scheduler.py) : l'agent vocal reste prioritaire.
    """
    app = settings.app()
    prefs = settings.user_prefs(chat.owner_id)
    docs = [f for f in files if f.kind == "document"]
    plan = await _plan(chat, docs, prefs)

    job = scheduler.enqueue(chat.owner_id, weight=2 if plan.heavy else 1, heavy=plan.heavy)
    try:
        started, shown, announced = time.monotonic(), None, False
        while not job.started:
            if time.monotonic() - started > app.queue_timeout_seconds:
                yield ("\n\n> ⚠️ Le serveur est très sollicité en ce moment (appels en cours ou autres demandes). "
                       "Réessayez dans quelques minutes.")
                return
            state = (scheduler.position(job), voice.calls())
            if state != shown:                                    # une ligne d'état à chaque changement seulement
                if not announced:
                    yield "```attente\n"
                    announced = True
                yield json.dumps({"status": "waiting", "position": state[0], "calls": state[1], "heavy": plan.heavy}) + "\n"
                shown = state
            try:
                await asyncio.wait_for(job.event.wait(), 3)
            except asyncio.TimeoutError:
                pass
        if announced:
            yield json.dumps({"status": "done"}) + "\n```\n\n"

        async for part in _generate(chat, files, web_mode, prefs, plan, docs, sink):
            yield part
    finally:
        scheduler.finish(job)                                     # libère la place, ou quitte la file si on a interrompu


async def _tool_loop(messages: list[dict], tools: list[dict], index: dict, actor) -> AsyncIterator[str]:
    """
    Le modèle peut appeler les outils (opérations des connexions API) : le serveur les exécute, renvoie les résultats au modèle,
    qui rédige sa réponse. Chaque appel laisse une carte dans la conversation (jamais le résultat brut).
    """
    for round_no in range(MAX_TOOL_ROUNDS + 1):
        offered = tools if round_no < MAX_TOOL_ROUNDS else None       # dernier tour : plus d'outil, le modèle doit répondre
        text, calls = "", []
        events = ollama.stream_events(messages, offered)
        try:
            async for kind, payload in events:
                if kind == "text":
                    text += payload
                    yield payload
                else:
                    calls += payload
        finally:
            await events.aclose()
        if not calls:
            return
        messages.append({"role": "assistant", "content": text,
                         "tool_calls": [{"function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls]})
        for c in calls[:MAX_CALLS_PER_ROUND]:
            with SessionLocal() as db:
                out = await connections.execute(db, actor, c["name"], c["arguments"], index)
            yield "\n\n```outil\n" + json.dumps(out.card, ensure_ascii=False) + "\n```\n\n"
            messages.append({"role": "tool", "tool_name": c["name"], "content": out.text})


async def _generate(chat: Chat, files: list[File], web_mode: str, prefs: settings.UserPrefs,
                    plan: Plan, docs: list[File], sink=None) -> AsyncIterator[str]:
    app = settings.app()

    # Documents : entiers s'ils sont courts, sinon lecture ciblée, synthèse ou relevé complet (voir documents.py)
    doc_context, blocks, history_budget = "", [], None
    if docs:
        budget = config.DOC_FULL_BUDGET if plan.heavy else config.DOC_CHAR_BUDGET
        history_budget = config.HISTORY_FULL_BUDGET if plan.heavy else None
        opened = False
        async for kind, payload in documents.prepare(docs, plan.search_query, plan.question, plan.mode, budget):
            if kind == "context":
                doc_context = payload
                continue
            if not opened:
                yield "```lecture\n"
                opened = True
            if kind == "progress":
                yield json.dumps({"status": "reading", **payload}, ensure_ascii=False) + "\n"
            else:
                blocks.append(payload)
        if opened:
            yield json.dumps({"status": "done", "docs": blocks}, ensure_ascii=False) + "\n```\n\n"

    if not app.analysis_enabled:
        files = [f for f in files if f.kind != "data"]
    data_files = [(file_path(f), f.stored_name) for f in files if f.kind == "data"]
    # Le code s'exécute dans le bac à sable pour analyser un fichier joint, ou pour tracer un graphique demandé
    code_mode = "data" if data_files else ("chart" if app.analysis_enabled and CHART_RX.search(plan.question or "") else None)
    doc_request = (code_mode is None and bool(DOC_REQUEST_RX.search(plan.question or ""))
                   and not documents.OVERVIEW_RX.search(plan.question or ""))
    # Connexions API (Wipsos…) : outils proposés au modèle si l'utilisateur y a accès
    tools, tool_index, actor = [], {}, None
    if not doc_request and code_mode is None:
        with SessionLocal() as tdb:
            tools, tool_index = connections.build_tools(tdb, chat.owner_id, prefs.disabled_connections)
            owner = tdb.get(User, chat.owner_id)
            actor = type("Actor", (), {"id": chat.owner_id, "username": owner.username if owner else ""})()
    messages = build_messages(chat, files, prefs, doc_context, history_budget, code_mode, doc_request, bool(tools))
    if not app.web_enabled or (doc_request and web_mode != "on"):   # pas de recherche web (ni de [1]) dans un document à envoyer
        web_mode = "off"
    if tools:
        # Les données des connexions sont confidentielles : jamais dans une requête vers un moteur de recherche public.
        # Recherche automatique coupée pour ces utilisateurs ; forcée (globe) seulement si la conversation n'a consulté aucune connexion.
        used_before = any("```outil" in m.content for m in chat.messages if m.role == "assistant")
        if web_mode == "auto" or used_before:
            web_mode = "off"

    query = None
    if web_mode != "off":
        query = await web.decide(messages[1:], has_files=bool(files), forced=web_mode == "on")
    if query:
        # Encart « recherche » affiché avant la réponse (une ligne JSON par état)
        yield "```recherche\n" + json.dumps({"status": "searching", "query": query, "auto": web_mode == "auto"},
                                             ensure_ascii=False) + "\n"
        query, results = await web.search(query)
        shown = [{"title": r["title"], "url": r["url"]} for r in results]
        yield json.dumps({"status": "done", "query": query, "results": shown}, ensure_ascii=False) + "\n```\n\n"
        messages[0]["content"] += web.format_for_model(query, results)

    if tools:
        async for part in _tool_loop(messages, tools, tool_index, actor):
            yield part
        return

    if doc_request:
        # Le serveur encadre le document et ajoute les liens : le modèle n'écrit que le contenu (voir DOC_ONLY_PROMPT).
        tokens = ollama.stream_chat(messages)
        text = ""
        try:
            first = await anext(tokens, None)
            if first is None or first.lstrip().startswith("> ⚠️"):       # erreur du modèle : pas de cadre autour
                yield first or "\n\n> ⚠️ Le modèle n'a rien répondu. Réessayez."
                return
            yield "<document>\n"
            text = first
            yield first
            async for token in tokens:
                text += token
                yield token
        finally:
            await tokens.aclose()
        links = "[Télécharger en Word](#telecharger-docx) · [Télécharger en PDF](#telecharger-pdf)"
        if exports.has_table(exports.trim_document(text)):
            links += " · [Télécharger en Excel](#telecharger-xlsx)"
        yield "\n</document>\n\n" + links
        return

    steps = 0

    while True:
        step_text = ""
        tokens = ollama.stream_chat(messages)
        code = None
        try:
            async for token in tokens:
                if code_mode and steps < app.max_analysis_steps:
                    m = CODE_BLOCK.search(step_text + token)
                    if m:
                        # le bloc est complet : on n'affiche rien au-delà et on coupe le modèle
                        tail = (step_text + token)[:m.end()][len(step_text):]
                        step_text += tail
                        yield tail
                        code = m.group(1)
                        break
                step_text += token
                yield token
        finally:
            await tokens.aclose()

        if code is None:
            return

        steps += 1
        ok, output, produced = await sandbox.run_analysis(code, data_files)
        log.info("Analyse %d/%d : %s, %d fichier(s)", steps, app.max_analysis_steps, "ok" if ok else "erreur", len(produced))
        yield f"\n\n```resultat\n{output}\n```\n\n"

        saved = []
        if produced and sink is not None:
            for name, data in produced:
                try:
                    saved.append(await asyncio.to_thread(sink.save, name, data))
                except Exception:
                    log.exception("Enregistrement du fichier %s impossible", name)
        if saved:
            yield "```fichiers\n" + json.dumps(saved, ensure_ascii=False) + "\n```\n\n"
        made = ("\nFichiers créés et présentés à l'utilisateur : "
                + ", ".join(f"{s['name']} ({'image affichée' if s['image'] else 'à télécharger'})" for s in saved)
                + ". Ne les recopie pas : présente-les en une phrase.") if saved else ""

        messages.append({"role": "assistant", "content": step_text})
        if ok:
            follow = ("Résultat de l'exécution :\n```\n" + output + "\n```\n" + made +
                      "\nRédige maintenant ta réponse à partir de ces résultats "
                      "(ou écris un autre bloc de code si un calcul manque).")
        else:
            follow = ("L'exécution a échoué :\n```\n" + output + "\n```\n"
                      + ("Corrige le code et réessaie." if steps < app.max_analysis_steps
                         else "Nombre d'essais atteint : explique le problème à l'utilisateur sans inventer de résultat."))
        messages.append({"role": "user", "content": follow})
