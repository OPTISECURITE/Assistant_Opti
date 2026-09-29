"""
Construction du contexte envoyé au modèle et boucle d'analyse de données.

1. Contexte : consigne système + documents joints (texte extrait) + profil
   des fichiers de données + historique, dans un budget de caractères.
2. Boucle : si le modèle écrit un bloc ```python, on l'arrête dès que le bloc
   est fermé (pour qu'il n'invente pas de résultat), on exécute le code dans
   le bac à sable, on affiche la sortie puis on lui redonne la main. Au plus
   MAX_ANALYSIS_STEPS exécutions.
"""
import json
import logging
import re
from typing import AsyncIterator

from . import config, documents, ollama, sandbox, settings, web
from .files import file_path
from .models import Chat, File

log = logging.getLogger("opti.agent")

CODE_BLOCK = re.compile(r"```python[^\n]*\n(.*?)```", re.S)
SEARCH_BLOCK = re.compile(r"```(?:recherche|lecture)\n.*?```\n*", re.S)   # encarts d'affichage, retirés de l'historique

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


DOC_PROMPT = """
## Documents joints par l'utilisateur
Appuie-toi sur ces documents pour répondre. Quand tu reprends une information, indique la page concernée entre
parenthèses, par exemple (p. 12), uniquement si ce numéro figure dans les extraits fournis. N'invente rien : si le
document ne contient pas la réponse, dis-le.
"""


def build_messages(chat: Chat, files: list[File], prefs: settings.UserPrefs | None = None,
                   doc_context: str = "", history_budget: int | None = None) -> list[dict]:
    history_budget = history_budget or config.HISTORY_CHAR_BUDGET
    app = settings.app()
    data = [f for f in files if f.kind == "data"] if app.analysis_enabled else []

    system = app.system_prompt + (settings.prefs_prompt(prefs) if prefs else "")
    if doc_context:
        system += DOC_PROMPT + doc_context
    if data:
        system += ANALYSIS_PROMPT
        for f in data:
            system += f"\n### Fichier de données « {f.filename} » → /data/{f.stored_name}\n{f.text[:4000]}\n"

    # Historique : on garde les messages les plus récents dans le budget
    history, used = [], 0
    for m in reversed(chat.messages):
        if not m.content:
            continue
        if used + len(m.content) > history_budget and history:
            break
        content = SEARCH_BLOCK.sub("", m.content) if m.role == "assistant" else m.content
        history.append({"role": m.role, "content": content})
        used += len(m.content)
    history.reverse()
    if history and history[0]["role"] == "assistant":
        history.pop(0)
    return [{"role": "system", "content": system}, *history]


async def run(chat: Chat, files: list[File], web_mode: str = "auto") -> AsyncIterator[str]:
    """web_mode : 'auto' (le modèle décide), 'on' (recherche forcée), 'off' (jamais)."""
    app = settings.app()

    # Documents : entiers s'ils sont courts, sinon lecture ciblée, synthèse ou relevé complet (voir documents.py)
    prefs = settings.user_prefs(chat.owner_id)
    doc_context, blocks, history_budget = "", [], None
    docs = [f for f in files if f.kind == "document"]
    if docs:
        user_msgs = [m.content for m in chat.messages if m.role == "user"]
        question = user_msgs[-1] if user_msgs else ""
        search_query = question if len(question) >= 30 or len(user_msgs) < 2 else user_msgs[-2] + " " + question
        light_limit = max(3000, config.DOC_CHAR_BUDGET // len(docs))
        mode = documents.EXTRAITS
        if any(len(f.text) > light_limit for f in docs):          # au moins un document trop long pour tenir en entier
            mode = await documents.decide_mode(question, documents.conversation_excerpt(chat.messages), prefs.doc_mode)
        heavy = mode != documents.EXTRAITS
        budget = config.DOC_FULL_BUDGET if heavy else config.DOC_CHAR_BUDGET
        history_budget = config.HISTORY_FULL_BUDGET if heavy else None
        opened = False
        async for kind, payload in documents.prepare(docs, search_query, question, mode, budget):
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

    messages = build_messages(chat, files, prefs, doc_context, history_budget)
    if not app.web_enabled:
        web_mode = "off"
    if not app.analysis_enabled:
        files = [f for f in files if f.kind != "data"]

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

    data_files = [(file_path(f), f.stored_name) for f in files if f.kind == "data"]
    steps = 0

    while True:
        step_text = ""
        tokens = ollama.stream_chat(messages)
        code = None
        try:
            async for token in tokens:
                if data_files and steps < app.max_analysis_steps:
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
        ok, output = await sandbox.run_code(code, data_files)
        log.info("Analyse %d/%d : %s", steps, app.max_analysis_steps, "ok" if ok else "erreur")
        yield f"\n\n```resultat\n{output}\n```\n\n"

        messages.append({"role": "assistant", "content": step_text})
        if ok:
            follow = ("Résultat de l'exécution :\n```\n" + output + "\n```\n"
                      "Rédige maintenant ta réponse à partir de ces résultats "
                      "(ou écris un autre bloc de code si un calcul manque).")
        else:
            follow = ("L'exécution a échoué :\n```\n" + output + "\n```\n"
                      + ("Corrige le code et réessaie." if steps < app.max_analysis_steps
                         else "Nombre d'essais atteint : explique le problème à l'utilisateur sans inventer de résultat."))
        messages.append({"role": "user", "content": follow})
