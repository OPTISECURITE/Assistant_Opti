"""
Client Ollama : relaie une conversation vers le modèle et renvoie les tokens
au fil de l'eau. Les erreurs d'Ollama sont converties en message lisible
pour l'utilisateur plutôt qu'en coupure silencieuse.
"""
import json
import logging
from typing import AsyncIterator

import httpx

from . import config

log = logging.getLogger("opti.ollama")

# Connexion : courte. Lecture : illimitée (un long texte peut prendre du temps).
TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)


async def stream_chat(messages: list[dict]) -> AsyncIterator[str]:
    payload = {
        "model": config.MODEL,
        "messages": messages,   # consigne système incluse par agent.build_messages
        "stream": True,
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            async with client.stream("POST", f"{config.OLLAMA_URL}/api/chat", json=payload) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode(errors="replace")
                    log.error("Ollama %s : %s", resp.status_code, body[:500])
                    yield _error_message(body)
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
                        yield _error_message(str(data["error"]))
                        return
                    token = data.get("message", {}).get("content", "")
                    if token:
                        yield token
                    if data.get("done"):
                        return
    except httpx.ConnectError:
        log.error("Ollama injoignable sur %s", config.OLLAMA_URL)
        yield "\n\n> ⚠️ Le modèle est momentanément indisponible. Réessayez dans un instant."


def _error_message(raw: str) -> str:
    if "context" in raw and ("exceed" in raw or "size" in raw):
        return ("\n\n> ⚠️ La conversation est devenue trop longue pour le modèle. "
                "Démarrez une nouvelle conversation pour continuer.")
    return "\n\n> ⚠️ Le modèle a renvoyé une erreur. Réessayez ou reformulez votre demande."


async def complete(messages: list[dict]) -> str:
    """Appel court, non diffusé (utilisé pour générer une requête de recherche)."""
    payload = {"model": config.MODEL, "messages": messages, "stream": False,
               "options": {"temperature": 0.2, "num_predict": 60}}
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)) as client:
        r = await client.post(f"{config.OLLAMA_URL}/api/chat", json=payload)
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "").strip()
