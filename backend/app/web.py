"""
Recherche web : requête générée par le modèle, interrogation de SearXNG,
lecture des meilleures pages et mise en forme pour le modèle.

Sécurité : le serveur ne lit une page que si son adresse résout vers une IP
publique (protection SSRF : un résultat piégé ne doit pas pouvoir faire
interroger Ollama, le PBX ou une autre machine du réseau interne).
"""
import asyncio
import ipaddress
import logging
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from . import config, ollama

log = logging.getLogger("opti.web")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AssistantOpti/1.0)", "Accept-Language": "fr-FR,fr;q=0.9"}

QUERY_PROMPT = (
    "Tu génères une requête pour un moteur de recherche web public, à partir de la dernière question "
    "de l'utilisateur (et du contexte de la conversation si nécessaire).\n"
    "Règles : 3 à 8 mots-clés, en français sauf si le sujet est anglophone, sans guillemets. "
    "N'inclus JAMAIS de nom de personne, de client, d'adresse, d'e-mail ni d'information interne à l'entreprise : "
    "remplace-les par des termes génériques.\n"
    "Réponds uniquement par la requête, sans aucun autre texte."
)


# ── Requête ──────────────────────────────────────────────────────────────────
async def make_query(history: list[dict]) -> str:
    recent = [m for m in history if m["role"] in ("user", "assistant")][-4:]
    convo = "\n".join(f"{'Utilisateur' if m['role'] == 'user' else 'Assistant'} : {m['content'][:800]}" for m in recent)
    try:
        q = await ollama.complete([
            {"role": "system", "content": QUERY_PROMPT},
            {"role": "user", "content": convo},
        ])
    except Exception:
        log.exception("Génération de la requête impossible")
        q = ""
    q = " ".join(q.replace('"', " ").split())[:200]
    return q or recent[-1]["content"][:200]


# ── SearXNG ──────────────────────────────────────────────────────────────────
async def searxng(query: str) -> list[dict]:
    params = {"q": query, "format": "json", "language": "fr", "safesearch": 1, "pageno": 1}
    async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
        r = await client.get(f"{config.SEARXNG_URL}/search", params=params)
        r.raise_for_status()
        data = r.json()
    results, seen = [], set()
    for item in sorted(data.get("results", []), key=lambda x: x.get("score", 0), reverse=True):
        url = item.get("url", "")
        if not url.startswith(("http://", "https://")) or url in seen or _obviously_internal(url):
            continue
        seen.add(url)
        results.append({"title": (item.get("title") or url)[:200], "url": url,
                        "snippet": (item.get("content") or "")[:500]})
        if len(results) >= config.WEB_RESULTS:
            break
    return results


def _obviously_internal(url: str) -> bool:
    """Écarte d'office les résultats pointant vers localhost ou une IP privée."""
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost",) or host.endswith((".local", ".lan", ".internal")):
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


# ── Lecture des pages (avec protection SSRF) ─────────────────────────────────
def _is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            return False
    return True


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside", "iframe"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article"}

    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        pass
    text = "".join(p.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


async def fetch_page(url: str) -> str:
    """Lit une page publique (redirections vérifiées une à une), renvoie son texte."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0), headers=HEADERS, follow_redirects=False) as client:
        for _ in range(4):
            host = urlparse(url).hostname or ""
            if not await asyncio.to_thread(_is_public, host):
                log.warning("Page ignorée (adresse non publique) : %s", url)
                return ""
            async with client.stream("GET", url) as r:
                if r.is_redirect and "location" in r.headers:
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
                    return ""
                body = b""
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > 1_500_000:
                        break
                return html_to_text(body.decode(r.encoding or "utf-8", errors="replace"))
    return ""


async def search(history: list[dict]) -> tuple[str, list[dict]]:
    query = await make_query(history)
    try:
        results = await searxng(query)
    except Exception:
        log.exception("SearXNG injoignable")
        return query, []

    async def read(r):
        try:
            text = await fetch_page(r["url"])
        except Exception:
            text = ""
        r["content"] = text[:config.WEB_PAGE_CHARS]

    await asyncio.gather(*(read(r) for r in results[:config.WEB_PAGES_READ]))
    return query, results


def format_for_model(query: str, results: list[dict]) -> str:
    if not results:
        return ("\n\n## Recherche web\nLa recherche n'a donné aucun résultat exploitable. "
                "Dis-le à l'utilisateur et réponds avec prudence à partir de tes connaissances.\n")
    out = [f"\n\n## Résultats de recherche web (requête : « {query} »)",
           "Appuie-toi sur ces sources et cite-les avec leur numéro entre crochets, par exemple [1]. "
           "Si elles ne suffisent pas ou se contredisent, dis-le. N'invente pas de source."]
    for i, r in enumerate(results, 1):
        body = r.get("content") or r["snippet"]
        out.append(f"\n[{i}] {r['title']}\n{r['url']}\n{body}")
    return "\n".join(out) + "\n"
