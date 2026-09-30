"""
Client Ollama : relaie une conversation vers le modèle et renvoie les tokens
au fil de l'eau. Les erreurs d'Ollama sont converties en message lisible
pour l'utilisateur plutôt qu'en coupure silencieuse.
"""
import json
import logging
from typing import AsyncIterator

import httpx

from . import config, settings

log = logging.getLogger("opti.ollama")

# Connexion : courte. Lecture : illimitée (un long texte peut prendre du temps).
TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)


async def stream_chat(messages: list[dict]) -> AsyncIterator[str]:
    """Texte seul (voir stream_events pour les appels d'outils)."""
    events = stream_events(messages)
    try:
        async for kind, payload in events:
            if kind == "text":
                yield payload
    finally:
        await events.aclose()               # ferme aussitôt la requête vers Ollama si le consommateur s'arrête


async def stream_events(messages: list[dict], tools: list[dict] | None = None) -> AsyncIterator[tuple[str, object]]:
    """Événements du modèle : ('text', jeton) ou ('tool_calls', [{'name', 'arguments'}]). Les erreurs sortent en texte lisible."""
    s = settings.app()
    payload = {
        "model": s.model,
        "messages": messages,   # consigne système incluse par agent.build_messages
        "stream": True,
        "options": {"temperature": s.temperature},
    }
    if tools:
        payload["tools"] = tools
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            async with client.stream("POST", f"{config.OLLAMA_URL}/api/chat", json=payload) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode(errors="replace")
                    log.error("Ollama %s : %s", resp.status_code, body[:500])
                    yield "text", _error_message(body)
                    return
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "error" in data:
                        log.error("Ollama (flux) : %s", data["error"])
                        yield "text", _error_message(str(data["error"]))
                        return
                    msg = data.get("message", {})
                    token = msg.get("content", "")
                    if token:
                        yield "text", token
                    calls = []
                    for tc in msg.get("tool_calls") or []:
                        fn = tc.get("function", {})
                        args = fn.get("arguments") or {}
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except ValueError:
                                args = {}
                        if fn.get("name"):
                            calls.append({"name": fn["name"], "arguments": args})
                    if calls:
                        yield "tool_calls", calls
                    if data.get("done"):
                        return
    except httpx.ConnectError:
        log.error("Ollama injoignable sur %s", config.OLLAMA_URL)
        yield "text", "\n\n> ⚠️ Le modèle est momentanément indisponible. Réessayez dans un instant."


def _error_message(raw: str) -> str:
    if "context" in raw and ("exceed" in raw or "size" in raw):
        return ("\n\n> ⚠️ La conversation et les documents joints dépassent la capacité du modèle. "
                "Démarrez une nouvelle conversation ou joignez moins de documents.")
    return "\n\n> ⚠️ Le modèle a renvoyé une erreur. Réessayez ou reformulez votre demande."


async def complete(messages: list[dict], json_mode: bool = False, *, num_predict: int = 80, timeout: float = 60.0) -> str:
    """Appel non diffusé (décision de recherche web, résumés de sections)."""
    payload = {"model": settings.app().model, "messages": messages, "stream": False,
               "options": {"temperature": 0.1, "num_predict": num_predict}}
    if json_mode:
        payload["format"] = "json"
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=timeout, write=30.0, pool=10.0)) as client:
        r = await client.post(f"{config.OLLAMA_URL}/api/chat", json=payload)
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "").strip()
