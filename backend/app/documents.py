"""
Préparation des documents joints pour le modèle (contexte limité à ~16k tokens).

Un modèle ne peut pas lire 80 pages d'un coup : on le fait lire morceau par morceau,
puis on assemble. Trois modes, choisis selon la question :

- « entier »    : document court, donné tel quel.
- « extraits »  : une information précise → passages les plus pertinents (BM25) avec leurs pages.
- « synthese »  : vue d'ensemble → TOUT le document est lu, section par section (notes détaillées),
                  puis les notes sont fusionnées par niveaux si elles dépassent le contexte.
- « exhaustif » : la réponse dépend de tout le document (lister, relever, vérifier) → chaque section
                  est relue avec la question en tête, puis les relevés sont assemblés.

Tout ce qui n'a pas pu être lu (pages scannées sans OCR, limite de pages, section en échec) est
signalé au modèle ET à l'utilisateur.
"""
import asyncio
import json
import logging
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import AsyncIterator, Callable

from . import config, ollama
from .files import file_dir, file_meta
from .models import File

log = logging.getLogger("opti.documents")

EXTRAITS, SYNTHESE, EXHAUSTIF = "extraits", "synthese", "exhaustif"
CACHE_VERSION = 2

PAGE_RX = re.compile(r"^\[Page (\d+)\]\n", re.M)
OVERVIEW_RX = re.compile(
    r"\b(r[ée]sum\w*|synth[èe]se|de quoi (parle|traite)|(points?|id[ée]es?|enseignements?)\s+(cl[ée]s?|principa\w+|essentiel\w*)"
    r"|vue d.ensemble|en bref|l.essentiel|analys\w+\s+(ce|cet|cette|le|la|ces|les)\b|contenu (du|de ce)|pr[ée]sente[- ]moi"
    r"|explique\w*[- ]moi (ce|cet|cette|le|la)\b|explique\w* (ce|cet|cette|le|la) (document|pdf|texte|contrat|rapport|fichier)"
    r"|que (dit|contient|raconte) (ce|cet|cette|le|la)\b|quel est le sujet|dis[- ]moi ce que contient)", re.I)
FULL_RX = re.compile(
    r"\b(tout le (document|pdf|fichier|contrat|rapport|texte)|l.int[ée]gralit[ée]|int[ée]gralement|en entier|exhaustiv\w*"
    r"|de bout en bout|(tous|toutes) les|chaque (page|article|clause|section|chapitre|paragraphe)|liste\w* (tous|toutes)"
    r"|page par page|section par section)", re.I)

CHUNK_CHARS = 1400
CHUNK_OVERLAP = 200
SECTION_CHARS = 6000
GROUP_CHARS = 9000        # taille d'un groupe de notes fusionnées en une fois
MAP_CONCURRENCY = 2       # sections lues en parallèle (le GPU est partagé avec l'agent vocal)


@dataclass
class Chunk:
    page: int | None
    text: str


# ── Choix du mode ────────────────────────────────────────────────────────────
ROUTER_PROMPT = """Tu choisis comment lire un document long (trop grand pour être lu d'un coup) afin de répondre à la DERNIÈRE question de l'utilisateur.
- "extraits" : la question cherche UNE information précise (une valeur, une date, une clause, une définition, « que dit l'article X ») que quelques passages suffisent à trouver.
- "synthese" : l'utilisateur veut une vue d'ensemble du document (résumé, thèmes, structure, de quoi il parle).
- "exhaustif" : la réponse dépend de TOUT le document : lister ou compter toutes les occurrences, relever tous les risques, obligations, montants ou anomalies, comparer, vérifier l'ensemble, ou toute question qui ne serait pas complète sans avoir lu chaque page.
Réponds uniquement en JSON : {"mode": "extraits" | "synthese" | "exhaustif"}"""


def conversation_excerpt(messages) -> str:
    recent = [m for m in messages if m.role in ("user", "assistant") and m.content][-3:]
    return "\n".join(f"{'Utilisateur' if m.role == 'user' else 'Assistant'} : {m.content[:600]}" for m in recent)


async def decide_mode(question: str, excerpt: str, pref: str) -> str:
    if OVERVIEW_RX.search(question or ""):
        return SYNTHESE
    if FULL_RX.search(question or "") or pref == "full":
        return EXHAUSTIF
    try:
        raw = await ollama.complete([{"role": "system", "content": ROUTER_PROMPT},
                                     {"role": "user", "content": excerpt or question}],
                                    json_mode=True, num_predict=40, timeout=30)
        mode = json.loads(raw).get("mode")
    except Exception:
        log.exception("Choix du mode de lecture impossible")
        mode = None
    return mode if mode in (EXTRAITS, SYNTHESE, EXHAUSTIF) else EXTRAITS


# ── Découpage ────────────────────────────────────────────────────────────────
def split_pages(text: str) -> list[tuple[int | None, str]]:
    parts = PAGE_RX.split(text)
    if len(parts) == 1:
        return [(None, text.strip())]
    out = [(None, parts[0].strip())] if parts[0].strip() else []
    for i in range(1, len(parts), 2):
        out.append((int(parts[i]), parts[i + 1].strip()))
    return out


def page_count(text: str) -> int:
    pages = [p for p, _ in split_pages(text) if p]
    return max(pages) if pages else 0


def chunk_text(text: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page, body in split_pages(text):
        if not body:
            continue
        start = 0
        while start < len(body):
            end = min(len(body), start + CHUNK_CHARS)
            if end < len(body):
                cut = body.rfind("\n", start + CHUNK_CHARS - 300, end)
                if cut == -1:
                    cut = body.rfind(" ", start + CHUNK_CHARS - 200, end)
                if cut > start:
                    end = cut
            piece = body[start:end].strip()
            if piece:
                chunks.append(Chunk(page, piece))
            if end >= len(body):
                break
            start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


# ── Recherche des passages pertinents (BM25) ─────────────────────────────────
STOP = set("""le la les un une des du de d l et ou à a au aux en dans sur par pour avec sans sous ce cet cette ces
se sa son ses leur leurs qui que quoi dont où est sont être été ont avoir il elle ils elles on nous vous je tu ne
pas plus mais donc car ni si y comme quel quelle quels quelles the of and to in is document pdf fichier""".split())


def tokens(text: str) -> list[str]:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    out = []
    for w in re.findall(r"[a-z0-9]{2,}", t):
        if w in STOP:
            continue
        if len(w) > 4 and w.endswith(("s", "x")):
            w = w[:-1]
        out.append(w)
    return out


def rank(chunks: list[Chunk], query: str) -> list[int]:
    q = set(tokens(query))
    if not q or not chunks:
        return []
    docs = [Counter(tokens(c.text)) for c in chunks]
    lengths = [sum(d.values()) for d in docs]
    n = len(docs)
    avg = (sum(lengths) / n) or 1
    df = Counter(w for d in docs for w in q if w in d)
    scored = []
    for i, d in enumerate(docs):
        s = 0.0
        for w in q:
            f = d.get(w, 0)
            if f:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                s += idf * f * 2.5 / (f + 1.5 * (0.25 + 0.75 * lengths[i] / avg))
        if s > 0:
            scored.append((-s, i))
    return [i for _, i in sorted(scored)]


def select(chunks: list[Chunk], query: str, budget: int) -> tuple[list[int], bool]:
    """Indices des passages retenus (dans l'ordre du document) et indicateur « au moins un mot en commun »."""
    order = rank(chunks, query)
    matched = bool(order)
    if not order:
        order = list(range(len(chunks)))
    chosen, used = [], 0
    for i in order:
        size = len(chunks[i].text) + 12
        if used + size > budget:
            continue
        chosen.append(i)
        used += size
    if 0 not in chosen and used + len(chunks[0].text) <= budget:
        chosen.append(0)
    return sorted(chosen), matched


def render_chunks(chunks: list[Chunk], indices: list[int]) -> str:
    blocks = []
    for i in indices:
        c = chunks[i]
        blocks.append((f"[Page {c.page}]\n" if c.page else "") + c.text)
    return "\n\n[…]\n\n".join(blocks)


# ── Lecture section par section ──────────────────────────────────────────────
def make_sections(text: str) -> list[tuple[str, str]]:
    """Regroupe les pages en sections d'environ SECTION_CHARS caractères : [(libellé des pages, texte)]."""
    pages = split_pages(text)
    if len(pages) == 1 and pages[0][0] is None:   # pas de pages : on tranche le texte
        body = pages[0][1]
        pages = [(None, body[i:i + SECTION_CHARS]) for i in range(0, len(body), SECTION_CHARS)]
    target = max(SECTION_CHARS, math.ceil(sum(len(b) for _, b in pages) / config.MAX_SECTIONS))
    sections, cur, size = [], [], 0
    for page, body in pages:
        cur.append((page, body))
        size += len(body)
        if size >= target:
            sections.append(cur)
            cur, size = [], 0
    if cur:
        sections.append(cur)
    out = []
    for sec in sections:
        nums = [p for p, _ in sec if p]
        label = ("p. " + (str(nums[0]) if nums[0] == nums[-1] else f"{nums[0]}–{nums[-1]}")) if nums else ""
        out.append((label, "\n\n".join((f"[Page {p}]\n" if p else "") + b for p, b in sec)))
    return out


async def _map(items: list[str], build: Callable[[str], list[dict]], num_predict: int, phase: str, doc: str,
               timeout: float = 180) -> AsyncIterator[tuple[str, object]]:
    """
    Interroge le modèle sur chaque élément, MAP_CONCURRENCY à la fois.
    Émet ('progress', {...}) au fil de l'eau, puis ('results', [texte par élément, "" si échec]).
    """
    n = len(items)
    results = [""] * n
    queue: asyncio.Queue = asyncio.Queue()
    slots = asyncio.Semaphore(MAP_CONCURRENCY)

    async def work(i: int):
        async with slots:
            try:
                out = await ollama.complete(build(items[i]), num_predict=num_predict, timeout=timeout)
            except Exception:
                log.exception("Lecture de l'élément %d/%d impossible", i + 1, n)
                out = ""
        await queue.put((i, out))

    tasks = [asyncio.create_task(work(i)) for i in range(n)]
    try:
        yield "progress", {"doc": doc, "phase": phase, "done": 0, "total": n}
        for k in range(n):
            i, out = await queue.get()
            results[i] = out
            yield "progress", {"doc": doc, "phase": phase, "done": k + 1, "total": n}
    finally:                      # l'utilisateur a interrompu : on arrête les appels en cours
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    yield "results", results


NOTES_PROMPT = (
    "Tu prends des notes détaillées sur une section d'un document professionnel, pour qu'on puisse ensuite en faire un "
    "résumé complet sans relire le texte. Écris en français des notes structurées en puces (150 à 220 mots) : sujet de la "
    "section, points et règles importants, chiffres, montants, délais, dates, noms, parties concernées, engagements, "
    "exceptions. Indique entre parenthèses la page quand tu la connais, par exemple (p. 12). N'invente rien et n'omets "
    "aucun chiffre ni délai. Réponds uniquement par les notes, sans introduction."
)
EXTRACT_PROMPT = (
    "Tu relèves, dans une section d'un document, tout ce qui concerne la demande de l'utilisateur, pour construire une "
    "réponse exhaustive.\nDemande : « {question} »\n"
    "Écris en français des puces factuelles (reformulation fidèle, avec chiffres, montants, délais, noms) en indiquant la "
    "page entre parenthèses, par exemple (p. 12). Ne recopie pas ce qui est hors sujet. S'il n'y a RIEN de pertinent dans "
    "cette section, réponds exactement : RIEN"
)
CONDENSE_PROMPT = (
    "Tu fusionnes des notes de lecture successives d'un même document en notes plus courtes (environ {words} mots), sans "
    "perdre les chiffres, montants, délais, dates, noms ni engagements importants, et en conservant les numéros de page.{focus} "
    "Réponds uniquement par les notes fusionnées."
)


def _size(items: list[dict]) -> int:
    return sum(len(i["summary"]) + len(i["pages"]) + 6 for i in items)


def _label_union(items: list[dict]) -> str:
    nums = [int(n) for it in items for n in re.findall(r"\d+", it["pages"])]
    if not nums:
        return ""
    lo, hi = min(nums), max(nums)
    return f"p. {lo}" if lo == hi else f"p. {lo}–{hi}"


def _group(items: list[dict], limit: int = GROUP_CHARS) -> list[list[dict]]:
    groups, cur, size = [], [], 0
    for it in items:
        s = len(it["summary"]) + len(it["pages"]) + 6
        if cur and size + s > limit:
            groups.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += s
    if cur:
        groups.append(cur)
    return groups


def notes_block(items: list[dict]) -> str:
    return "\n".join(f"- {(i['pages'] + ' : ') if i['pages'] else ''}{i['summary']}" for i in items)


async def condense(items: list[dict], budget: int, doc: str, focus: str = "") -> AsyncIterator[tuple[str, object]]:
    """Fusionne les notes voisines, niveau par niveau, tant que le total dépasse `budget`. Émet progress puis ('items', …)."""
    for _ in range(4):
        if _size(items) <= budget or len(items) <= 1:
            break
        groups = _group(items)
        if len(groups) >= len(items):
            break
        texts = [notes_block(g) for g in groups]
        words = max(100, int(budget / len(groups) / 6 * 0.8))
        prompt = CONDENSE_PROMPT.format(words=words, focus=f" Ces notes répondent à la demande : « {focus[:300]} »." if focus else "")
        merged: list[str] = []
        async for kind, payload in _map(texts, lambda t: [{"role": "system", "content": prompt}, {"role": "user", "content": t}],
                                        min(900, int(words * 2.2)), "condensation", doc):
            if kind == "progress":
                yield kind, payload
            else:
                merged = payload
        items = [{"pages": _label_union(g), "summary": (out.strip() or texts[k][:budget // len(groups)])}
                 for k, (g, out) in enumerate(zip(groups, merged))]
    if _size(items) > budget and items:                       # dernier recours : troncature proportionnelle
        cap = max(200, budget // len(items) - 20)
        items = [{**i, "summary": i["summary"][:cap] + "…"} for i in items]
    yield "items", items


async def read_notes(f: File) -> AsyncIterator[tuple[str, object]]:
    """Notes détaillées de TOUTES les sections (mises en cache). Émet progress puis ('notes', (notes, échecs))."""
    cache = file_dir(f.id) / "summary.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("v") == CACHE_VERSION and data.get("sections"):
            yield "notes", (data["sections"], 0)
            return
    except Exception:
        pass
    sections = make_sections(f.text)
    results: list[str] = []
    async for kind, payload in _map([b for _, b in sections],
                                    lambda body: [{"role": "system", "content": NOTES_PROMPT}, {"role": "user", "content": body}],
                                    450, "lecture", f.filename):
        if kind == "progress":
            yield kind, payload
        else:
            results = payload
    failed = sum(1 for out in results if not out.strip())
    notes = [{"pages": label, "summary": out.strip() or (" ".join(body.split())[:700] + "…")}
             for (label, body), out in zip(sections, results)]
    if not failed:                                            # on ne garde en cache que les lectures complètes
        try:
            cache.write_text(json.dumps({"v": CACHE_VERSION, "sections": notes}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            log.warning("Cache des notes non écrit pour %s", f.id)
    yield "notes", (notes, failed)


async def read_for_question(f: File, question: str) -> AsyncIterator[tuple[str, object]]:
    """Relit TOUTES les sections avec la question en tête. Émet progress puis ('found', (relevés, échecs, nb sections))."""
    sections = make_sections(f.text)
    prompt = EXTRACT_PROMPT.format(question=question.replace("\n", " ")[:400])
    results: list[str] = []
    async for kind, payload in _map([b for _, b in sections],
                                    lambda body: [{"role": "system", "content": prompt}, {"role": "user", "content": body}],
                                    500, "lecture", f.filename):
        if kind == "progress":
            yield kind, payload
        else:
            results = payload
    failed = sum(1 for out in results if not out.strip())
    found = [{"pages": label, "summary": out.strip()} for (label, _), out in zip(sections, results)
             if out.strip() and not out.strip().upper().startswith("RIEN")]
    yield "found", (found, failed, len(sections))


# ── Couverture de lecture ────────────────────────────────────────────────────
def coverage_notes(f: File) -> list[str]:
    m = file_meta(f)
    notes = []
    if m.get("truncated"):
        notes.append(f"seules les {m.get('read_pages') or config.MAX_PDF_PAGES} premières pages sur {m.get('pages')} ont été lues (limite de lecture)")
    if m.get("unread"):
        notes.append(f"{m['unread']} page(s) scannée(s) ou illisible(s) n'ont pas pu être lues (OCR désactivé, limité ou en échec)")
    if m.get("basic"):
        notes.append("lecture de secours : tableaux et pages scannées non pris en compte")
    return notes


def _warning(notes: list[str], failed: int = 0) -> str:
    all_notes = list(notes) + ([f"{failed} section(s) n'ont pas pu être analysées par le modèle"] if failed else [])
    if not all_notes:
        return ""
    return "⚠ LECTURE PARTIELLE : " + " ; ".join(all_notes) + ". Signale-le clairement à l'utilisateur dans ta réponse.\n"


# ── Point d'entrée ───────────────────────────────────────────────────────────
async def prepare(docs: list[File], search_query: str, question: str, mode: str, budget: int) -> AsyncIterator[tuple[str, object]]:
    """
    Émet ('progress', {...}) pendant la lecture, ('info', {...}) pour chaque document travaillé,
    puis ('context', texte) à insérer dans la consigne système.
    """
    per_doc = max(3000, budget // max(1, len(docs)))
    parts = []
    for f in docs:
        meta = file_meta(f)
        n = meta.get("pages") or page_count(f.text)
        title = f"\n### Document « {f.filename} »" + (f" ({n} pages)" if n else "")
        cover = coverage_notes(f)
        info = {"name": f.filename, "total_pages": n, "read_pages": meta.get("read_pages") or n, "partial": cover}

        if len(f.text) <= per_doc:                                   # tient en entier
            parts.append(title + "\n" + _warning(cover) + f.text + "\n")
            if cover:
                yield "info", {**info, "mode": "entier"}
            continue

        if mode == SYNTHESE:
            notes, failed = [], 0
            async for kind, payload in read_notes(f):
                if kind == "progress":
                    yield kind, payload
                else:
                    notes, failed = payload
            sections = len(notes)
            items = notes
            async for kind, payload in condense(items, per_doc - 900, f.filename):
                if kind == "progress":
                    yield kind, payload
                else:
                    items = payload
            condensed = len(items) < sections
            yield "info", {**info, "mode": SYNTHESE, "sections": sections, "failed": failed, "condensed": condensed}
            parts.append(
                title + " : lu en entier\n" + _warning(cover, failed)
                + f"Le document entier ({n or 'toutes les'} pages) a été lu section par section ({sections} sections"
                + (", notes fusionnées faute de place" if condensed else "") + "). Voici les notes de lecture :\n"
                + notes_block(items)
                + "\nRédige un résumé complet et structuré à partir de ces notes (grands thèmes, chiffres, délais, obligations, "
                  "exceptions), avec les pages. Adapte la longueur à la demande (« court », « détaillé »). "
                  "N'ajoute rien qui ne figure pas dans les notes.\n")
            continue

        if mode == EXHAUSTIF:
            found, failed, sections = [], 0, 0
            async for kind, payload in read_for_question(f, question):
                if kind == "progress":
                    yield kind, payload
                else:
                    found, failed, sections = payload
            hits = len(found)
            items = found
            async for kind, payload in condense(items, per_doc - 900, f.filename, focus=question):
                if kind == "progress":
                    yield kind, payload
                else:
                    items = payload
            yield "info", {**info, "mode": EXHAUSTIF, "sections": sections, "hits": hits, "failed": failed}
            body = notes_block(items) if items else "Aucun élément pertinent n'a été trouvé dans le document."
            parts.append(
                title + " : relevé sur l'ensemble du document\n" + _warning(cover, failed)
                + f"Le document entier a été relu section par section ({sections} sections) avec la demande de l'utilisateur "
                  f"en tête ; {hits} section(s) contiennent des éléments pertinents. Voici les relevés :\n" + body
                + "\nRédige une réponse complète et organisée qui regroupe TOUS ces éléments, sans en omettre, avec les pages. "
                  "N'invente rien qui ne figure pas dans les relevés.\n")
            continue

        # EXTRAITS : passages les plus proches de la question
        chunks = chunk_text(f.text)
        chosen, matched = select(chunks, search_query, per_doc)
        pages = sorted({chunks[i].page for i in chosen if chunks[i].page})
        yield "info", {**info, "mode": EXTRAITS, "pages": pages, "matched": matched}
        intro = (f"Le document est long : seuls les {len(chosen)} passages les plus proches de la question sont fournis "
                 f"(sur {len(chunks)}). Si l'information n'apparaît pas dans ces extraits, dis-le clairement au lieu de deviner "
                 "et propose à l'utilisateur de demander une lecture complète du document.\n") if matched else \
                ("Le document est long et aucun passage ne correspond précisément à la question : seul le début du document est fourni. "
                 "Dis-le à l'utilisateur et propose-lui une lecture complète du document.\n")
        parts.append(title + "\n" + _warning(cover) + intro + "\n" + render_chunks(chunks, chosen) + "\n")

    yield "context", "".join(parts)
