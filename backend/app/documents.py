"""
Préparation des documents joints pour le modèle (contexte limité à ~16k tokens).

- Document court : donné en entier.
- Document long, question précise : découpage en passages, classement par
  pertinence (BM25, sans modèle ni GPU) et envoi des meilleurs passages avec
  leur numéro de page.
- Document long, demande globale (« résume », « analyse ce document ») :
  lecture section par section par le modèle, puis synthèse. Le résultat est
  gardé sur disque pour ne pas être recalculé à chaque question.
"""
import json
import logging
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import AsyncIterator

from . import ollama
from .files import file_dir
from .models import File

log = logging.getLogger("opti.documents")

PAGE_RX = re.compile(r"^\[Page (\d+)\]\n", re.M)
OVERVIEW_RX = re.compile(
    r"\b(r[ée]sum\w*|synth[èe]se|de quoi (parle|traite)|(points?|id[ée]es?|enseignements?)\s+(cl[ée]s?|principa\w+|essentiel\w*)"
    r"|vue d.ensemble|en bref|l.essentiel|analys\w+\s+(ce|cet|cette|le|la|ces|les)\b|contenu (du|de ce)|pr[ée]sente[- ]moi)", re.I)

CHUNK_CHARS = 1400
CHUNK_OVERLAP = 200
SECTION_CHARS = 6000
MAX_SECTIONS = 14


@dataclass
class Chunk:
    page: int | None
    text: str


def is_overview(question: str) -> bool:
    return bool(OVERVIEW_RX.search(question or ""))


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
    """Indices des passages retenus (dans l'ordre du document) et indicateur « aucun mot en commun »."""
    order = rank(chunks, query)
    matched = bool(order)
    if not order:
        order = list(range(len(chunks)))   # aucune correspondance : on donne le début du document
    chosen, used = [], 0
    for i in order:
        size = len(chunks[i].text) + 12
        if used + size > budget:
            continue
        chosen.append(i)
        used += size
    if 0 not in chosen and used + len(chunks[0].text) <= budget:
        chosen.append(0)   # le début du document situe le reste
    return sorted(chosen), matched


def render_chunks(chunks: list[Chunk], indices: list[int]) -> str:
    blocks = []
    for i in indices:
        c = chunks[i]
        blocks.append((f"[Page {c.page}]\n" if c.page else "") + c.text)
    return "\n\n[…]\n\n".join(blocks)


# ── Synthèse section par section (documents longs) ───────────────────────────
SUMMARY_PROMPT = (
    "Tu résumes une section d'un document professionnel. Écris en français un résumé factuel de 80 à 120 mots maximum : "
    "sujets traités, chiffres, dates, noms, engagements et décisions importants. N'invente rien. "
    "Réponds uniquement par le résumé, sans introduction."
)


def make_sections(text: str) -> list[tuple[str, str]]:
    """Regroupe les pages en sections d'environ SECTION_CHARS caractères : [(libellé des pages, texte)]."""
    pages = split_pages(text)
    if len(pages) == 1 and pages[0][0] is None:   # pas de pages : on tranche le texte
        body = pages[0][1]
        pages = [(None, body[i:i + SECTION_CHARS]) for i in range(0, len(body), SECTION_CHARS)]
    target = max(SECTION_CHARS, math.ceil(sum(len(b) for _, b in pages) / MAX_SECTIONS))
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


async def summarize_sections(f: File) -> AsyncIterator[tuple[str, object]]:
    """Émet ('progress', {...}) puis ('summaries', [{pages, summary}])."""
    cache = file_dir(f.id) / "summary.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if isinstance(data, list) and data:
            yield "summaries", data
            return
    except Exception:
        pass

    sections = make_sections(f.text)
    results = []
    for i, (label, body) in enumerate(sections, 1):
        yield "progress", {"doc": f.filename, "done": i - 1, "total": len(sections)}
        try:
            summary = await ollama.complete(
                [{"role": "system", "content": SUMMARY_PROMPT}, {"role": "user", "content": body[:SECTION_CHARS * 2]}],
                num_predict=300, timeout=120)
        except Exception:
            log.exception("Résumé de la section %d impossible", i)
            summary = ""
        if not summary.strip():   # repli : début de la section, sans mise en forme
            summary = " ".join(body.split())[:500] + "…"
        results.append({"pages": label, "summary": summary.strip()})
    try:
        cache.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.warning("Cache de synthèse non écrit pour %s", f.id)
    yield "summaries", results


# ── Point d'entrée : contexte documentaire d'une question ────────────────────
async def prepare(docs: list[File], question: str, overview: bool, budget: int) -> AsyncIterator[tuple[str, object]]:
    """
    Émet ('progress', {...}) pendant la lecture, ('info', {...}) pour chaque document travaillé,
    et finalement ('context', texte) à insérer dans la consigne système.
    """
    per_doc = max(3000, budget // max(1, len(docs)))
    parts = []
    for f in docs:
        n = page_count(f.text)
        title = f"\n### Document « {f.filename} »" + (f" ({n} pages)" if n else "") + "\n"

        if len(f.text) <= per_doc:                       # tient en entier
            parts.append(title + f.text + "\n")
            continue

        if overview:
            summaries = []
            async for kind, payload in summarize_sections(f):
                if kind == "progress":
                    yield "progress", payload
                else:
                    summaries = payload
            yield "info", {"name": f.filename, "mode": "synthese", "sections": len(summaries), "total_pages": n}
            lines = "\n".join(f"- {s['pages'] + ' : ' if s['pages'] else ''}{s['summary']}" for s in summaries)
            parts.append(title + "Le document est trop long pour être fourni en entier. Voici un résumé de chacune de ses "
                         "sections, généré automatiquement (l'utilisateur peut poser une question précise pour obtenir le détail "
                         "d'un passage) :\n" + lines + "\n")
            continue

        chunks = chunk_text(f.text)
        chosen, matched = select(chunks, question, per_doc)
        pages = sorted({chunks[i].page for i in chosen if chunks[i].page})
        yield "info", {"name": f.filename, "mode": "extraits", "pages": pages, "total_pages": n, "matched": matched}
        intro = (f"Le document est long : seuls les {len(chosen)} passages les plus proches de la question sont fournis "
                 f"(sur {len(chunks)}). Si l'information n'apparaît pas dans ces extraits, dis-le clairement au lieu de deviner "
                 "et suggère de reformuler la question.\n") if matched else \
                ("Le document est long et aucun passage ne correspond précisément à la question : seul le début du document est fourni. "
                 "Dis-le à l'utilisateur et invite-le à préciser sa question.\n")
        parts.append(title + intro + "\n" + render_chunks(chunks, chosen) + "\n")

    yield "context", "".join(parts)
