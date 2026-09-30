"""
Export d'une réponse de l'assistant (Markdown) vers Word (.docx), PDF et Excel (.xlsx).

Le modèle rédige le contenu ; ce module le met en forme de façon déterministe (aucun code écrit par le modèle) : en-tête
avec le logo Opti, titres, listes, tableaux, blocs de code, pied de page avec la date et la mention « à vérifier ».
Le Markdown est d'abord converti en une structure de blocs, puis chaque format la restitue.
"""
import datetime
import io
import re
from pathlib import Path
from xml.sax.saxutils import escape

from markdown_it import MarkdownIt

from . import config, settings

BRAND = "1070aa"          # bleu Opti
LOGO = config.FRONTEND_DIR / "img" / "logo.png"


def _logo() -> Path | None:
    return LOGO if settings.app().export_logo and LOGO.exists() else None


def _footer() -> str:
    return settings.app().export_footer.strip()


def _footer_line(page: str | None) -> str:
    """Texte du pied de page : mention éventuelle et numéro de page éventuel (vide = pas de pied de page)."""
    parts = [_footer()] if _footer() else []
    if page is not None and settings.app().export_page_numbers:
        parts.append(f"Page {page}")
    return "   ·   ".join(parts)

# Blocs d'affichage et de calcul de la conversation : ils ne font pas partie du document
_MACHINERY = re.compile(r"```(?:recherche|lecture|attente|fichiers|outil|python|resultat)[^\n]*\n.*?(?:```\n*|\Z)", re.S)

_md = MarkdownIt("commonmark", {"breaks": True}).enable(["table", "strikethrough"])


class NoTable(Exception):
    """La réponse ne contient aucun tableau à exporter vers Excel."""


# Faux liens de téléchargement que le modèle invente parfois (« Télécharger en PDF » vers « # »). Ils n'ont aucun sens
# dans un document : le vrai téléchargement est le bouton de l'application.
_FAKE_LINK = re.compile(r"\[[^\]\n]*(?:télécharg|download)[^\]\n]*\]\((?:#|[^)\s]*#)\)", re.I)
_FAKE_NOTE = re.compile(r"placeholders?", re.I)
_FAKE_LEAD = re.compile(r"^\W*télécharger\s.{0,60}\sau format\s*:?\s*$", re.I)


_REAL_LINK = re.compile(r"\]\(#telecharger-(?:docx|pdf|xlsx)\)")     # liens actifs de l'application : jamais dans un document


def strip_fake_downloads(text: str) -> str:
    keep = [ln for ln in text.split("\n")
            if not (_FAKE_LINK.search(ln) or _REAL_LINK.search(ln)
                    or (_FAKE_NOTE.search(ln) and re.search(r"liens? ci-dessus", ln, re.I)) or _FAKE_LEAD.match(ln))]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(keep))


# ── Le document à exporter : ce que le modèle a mis entre <document> et </document> ─────────────────────────────
# Quand l'utilisateur demande une lettre, un compte rendu… le modèle place UNIQUEMENT le contenu du document entre ces
# balises, et met ses commentaires et conseils avant ou après : seul l'intérieur est exporté.
_DOC_RX = re.compile(r"<document(?:\s[^>]*)?>(.*?)(?:</document\s*>|\Z)", re.S | re.I)
_LEAD_IN = re.compile(r"^\s*(?:voici|bien sûr|certainement|d'accord|parfait|très bien|avec plaisir)\b[^\n]{0,220}[:.!]\s*$", re.I)
_TAIL = re.compile(r"^\W*(?:à noter|remarques?\s*:|conseils?\s*:|astuces?\s*:|n'hésitez pas|si vous (?:souhaitez|voulez|désirez)|pour aller plus loin|💡|📌)", re.I)


def _trim_framing(text: str) -> str:
    """Sans balises : retire la phrase d'introduction (« Voici la lettre : ») et le bloc de conseils final, s'ils sont évidents."""
    paras = re.split(r"\n{2,}", text.strip())
    if len(paras) > 1 and _LEAD_IN.match(paras[0]):
        paras = paras[1:]
    for i, p in enumerate(paras):
        if i >= max(1, len(paras) // 2) and _TAIL.match(p.strip()):
            paras = paras[:i]
            break
    return "\n\n".join(paras)


# ── Nettoyage rigoureux d'un document rédigé : rien avant le document, rien après sa signature ─────────────────
_LEAD_META = re.compile(r"^(?:voici|bien sûr|certainement|d'accord|parfait|très bien|avec plaisir|je vous propose|ci-dessous|proposition de|"
                        r"vous trouverez|(?:le )?texte (?:de la|du|ci-dessous))\b", re.I)
_CLOSING = re.compile(r"\b(?:cordialement|bien cordialement|salutations|bien à vous|respectueusement|sincères?\b|dans l'attente de votre|"
                      r"veuillez agréer|je vous prie d'agréer|amicalement|à bientôt)", re.I)
_META_TAIL = re.compile(r"^(?:à noter|remarques?|conseils?|astuces?|n'hésitez pas|si vous (?:souhaitez|voulez|désirez|avez besoin)|"
                        r"pour aller plus loin|je peux (?:aussi|également|vous)|cette (?:lettre|version|proposition|réponse)|"
                        r"ce (?:courrier|mail|message|document|modèle) (?:est|peut|doit|a été|vous)|vous pouvez (?:également|aussi|adapter|modifier|personnaliser)|"
                        r"adaptez|personnalisez|n'oubliez pas de (?:personnaliser|adapter|compléter)|il est (?:recommandé|conseillé|préférable) de (?:personnaliser|adapter|vérifier))", re.I)


def _plain_start(p: str) -> str:
    """Début d'un paragraphe sans la mise en forme Markdown, les puces ni les emojis."""
    return re.sub(r"^[\W_]+", "", p.strip())


def trim_document(text: str) -> str:
    """
    Ne garde que le document. Retire l'introduction (« Voici la lettre : ») et tout ce qui suit la signature :
    « À noter », conseils, remarques, propositions de modifications. Un document sans formule de politesse est
    seulement débarrassé de ses blocs de fin évidents.
    """
    paras = [p for p in re.split(r"\n{2,}", text.strip()) if p.strip()]
    while paras and (re.fullmatch(r"[-–—*_=\s]{3,}", paras[0].strip()) or
                     (_LEAD_META.match(_plain_start(paras[0])) and len(paras[0]) < 300)):
        paras.pop(0)
    closing = max((i for i, p in enumerate(paras) if _CLOSING.search(p)), default=None)
    if closing is not None:
        end = closing + 1                                   # la formule de politesse et, souvent, sa signature dans le même paragraphe
        if end < len(paras):
            sig = paras[end]
            lines = [ln for ln in sig.split("\n") if ln.strip()]
            if (len(lines) <= 4 and all(len(ln) <= 90 for ln in lines) and not _META_TAIL.match(_plain_start(sig))
                    and not re.match(r"^\W*[💡📌✅⚠ℹ]", sig) and not re.fullmatch(r"[-–—*_=\s]{3,}", sig.strip())):
                end += 1                                    # bloc de signature séparé (nom, fonction, téléphone…)
        paras = paras[:end]
    else:
        for i, p in enumerate(paras):
            if i >= max(1, len(paras) // 2) and _META_TAIL.match(_plain_start(p)):
                paras = paras[:i]
                break
        while paras and re.fullmatch(r"[-–—*_=\s]{3,}", paras[-1].strip()):
            paras.pop()
    while paras and re.fullmatch(r"[-–—*_=\s]{3,}", paras[-1].strip()):      # un trait de séparation ne termine pas un document
        paras.pop()
    return "\n\n".join(paras)


_OFFER = re.compile(r"télécharg|download", re.I)
_OFFER_HINT = re.compile(r"bouton|liens?\b|ci-dessous|ci-dessus|sous cette réponse|cette réponse|au format|en (?:word|pdf|excel)", re.I)


def strip_download_offers(text: str) -> str:
    """Réponse SANS document : retire les liens et les phrases qui proposent un téléchargement (il n'y a rien à télécharger)."""
    keep = [ln for ln in strip_fake_downloads(text).split("\n")
            if not (len(ln) < 220 and _OFFER.search(ln) and _OFFER_HINT.search(ln))]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(keep))


def tidy_answer(content: str) -> str:
    """Nettoie l'intérieur de chaque <document>…</document> d'une réponse enregistrée. Sans document, retire toute offre de téléchargement."""
    if content and not re.search(r"<document\b", content, re.I):
        return strip_download_offers(content)

    def fix(m):
        inner = trim_document(m.group(1))
        return f"<document>\n{inner}\n</document>" if inner else m.group(0)
    return re.sub(r"<document(?:\s[^>]*)?>(.*?)</document\s*>", fix, content or "", flags=re.S | re.I)


def prepare(content: str) -> tuple[str, bool]:
    """(texte à exporter, True si le modèle a délimité le document)."""
    text = _MACHINERY.sub("", content or "")
    docs = [trim_document(d) for d in _DOC_RX.findall(text) if d.strip()]
    docs = [d for d in docs if d.strip()]
    if docs:
        return strip_fake_downloads("\n\n---\n\n".join(docs)).strip(), True
    return _trim_framing(strip_fake_downloads(text)).strip(), False


def clean(text: str) -> str:
    return prepare(text)[0]


# ── Markdown → blocs ─────────────────────────────────────────────────────────
# Bloc : ("heading", niveau, runs) | ("para", runs) | ("list", ordonnée, début, [items]) | ("quote", [blocs])
#        ("code", texte) | ("hr",) | ("table", en-tête, lignes, alignements)
# Run : (texte, {"b", "i", "s", "code", "link", "br"})
def _inline(tok) -> list[tuple[str, dict]]:
    runs, b, i, s, link = [], 0, 0, 0, None
    for c in tok.children or []:
        t = c.type
        style = {"b": b > 0, "i": i > 0, "s": s > 0, "code": False, "link": link}
        if t == "text":
            runs.append((c.content, style))
        elif t == "code_inline":
            runs.append((c.content, {**style, "code": True}))
        elif t in ("softbreak", "hardbreak"):
            runs.append(("\n", {"br": True}))
        elif t == "strong_open":
            b += 1
        elif t == "strong_close":
            b -= 1
        elif t == "em_open":
            i += 1
        elif t == "em_close":
            i -= 1
        elif t == "s_open":
            s += 1
        elif t == "s_close":
            s -= 1
        elif t == "link_open":
            link = c.attrGet("href")
        elif t == "link_close":
            link = None
        elif t in ("image", "html_inline"):
            runs.append((c.content or "", style))
    return runs


def plain(runs) -> str:
    return "".join(("\n" if r[1].get("br") else r[0]) for r in runs)


def _blocks(tokens, i, stop):
    out = []
    while i < len(tokens):
        t = tokens[i]
        if stop and t.type == stop:
            return out, i
        if t.type == "heading_open":
            out.append(("heading", int(t.tag[1:]), _inline(tokens[i + 1])))
            i += 3
        elif t.type == "paragraph_open":
            out.append(("para", _inline(tokens[i + 1])))
            i += 3
        elif t.type in ("bullet_list_open", "ordered_list_open"):
            ordered = t.type == "ordered_list_open"
            start = int(t.attrGet("start") or 1)
            close = "ordered_list_close" if ordered else "bullet_list_close"
            i += 1
            items = []
            while tokens[i].type != close:
                inner, j = _blocks(tokens, i + 1, "list_item_close")
                items.append(inner)
                i = j + 1
            out.append(("list", ordered, start, items))
            i += 1
        elif t.type == "blockquote_open":
            inner, j = _blocks(tokens, i + 1, "blockquote_close")
            out.append(("quote", inner))
            i = j + 1
        elif t.type in ("fence", "code_block"):
            out.append(("code", t.content.rstrip("\n")))
            i += 1
        elif t.type == "hr":
            out.append(("hr",))
            i += 1
        elif t.type == "table_open":
            header, rows, aligns, cur, in_head = [], [], [], None, False
            i += 1
            while tokens[i].type != "table_close":
                x = tokens[i]
                if x.type == "thead_open":
                    in_head = True
                elif x.type == "thead_close":
                    in_head = False
                elif x.type == "tr_open":
                    cur = []
                elif x.type == "tr_close":
                    if in_head:
                        header = cur
                    else:
                        rows.append(cur)
                elif x.type in ("th_open", "td_open"):
                    style = x.attrGet("style") or ""
                    if in_head:
                        aligns.append("right" if "right" in style else "center" if "center" in style else "left")
                    cur.append(_inline(tokens[i + 1]))
                i += 1
            out.append(("table", header, rows, aligns))
            i += 1
        else:
            i += 1
    return out, i


def parse(text: str) -> list:
    return _blocks(_md.parse(text), 0, None)[0]


def _walk_tables(blocks):
    for b in blocks:
        if b[0] == "table":
            yield b
        elif b[0] == "list":
            for item in b[3]:
                yield from _walk_tables(item)
        elif b[0] == "quote":
            yield from _walk_tables(b[1])


def has_table(text: str) -> bool:
    return any(True for _ in _walk_tables(parse(clean(text))))


def _head(blocks: list, fallback: str, delimited: bool) -> tuple[str, list, bool]:
    """(titre, corps, afficher le bloc titre + date). Un document délimité sans titre # n'en reçoit pas : une lettre commence par sa 1re ligne."""
    starts_with_h1 = bool(blocks) and blocks[0][0] == "heading" and blocks[0][1] == 1
    if delimited and not starts_with_h1:
        return fallback, blocks, False
    title, body = split_title(blocks, fallback)
    return title, body, True


def split_title(blocks: list, fallback: str) -> tuple[str, list]:
    """Un premier titre de niveau 1 devient le titre du document (et n'est pas répété dans le corps)."""
    if blocks and blocks[0][0] == "heading" and blocks[0][1] == 1:
        return plain(blocks[0][2]).strip() or fallback, blocks[1:]
    return fallback, blocks


def _today() -> str:
    return datetime.date.today().strftime("%d/%m/%Y")


# ═════════════════════════════════════════════════════════════════════════════
# WORD
# ═════════════════════════════════════════════════════════════════════════════
def to_docx(text: str, title: str, delimited: bool = False) -> bytes:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    title, blocks, show_title = _head(parse(text), title or "Document", delimited)
    blue = RGBColor.from_string(BRAND.upper())
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin, sec.bottom_margin = Cm(2.4), Cm(2.2)

    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(11)
    st.paragraph_format.space_after = Pt(6)
    for name, size in (("Heading 1", 17), ("Heading 2", 14), ("Heading 3", 12), ("Heading 4", 11)):
        h = doc.styles[name]
        h.font.name, h.font.size, h.font.bold, h.font.color.rgb = "Calibri", Pt(size), True, blue
        h.paragraph_format.space_before, h.paragraph_format.space_after = Pt(14), Pt(5)
        h.paragraph_format.keep_with_next = True
        rf = h.element.get_or_add_rPr().find(qn("w:rFonts"))      # la police du thème l'emporterait sur « Calibri » dans Word
        if rf is not None:
            for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
                if rf.get(qn(attr)) is not None:
                    del rf.attrib[qn(attr)]

    def shade(el_pr, fill):
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), fill)
        el_pr.append(shd)

    def border(par, side, color="C9D6E2", size=8, space=4):
        pPr = par._p.get_or_add_pPr()
        bd = pPr.find(qn("w:pBdr"))
        if bd is None:
            bd = OxmlElement("w:pBdr"); pPr.append(bd)
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"), "single"); el.set(qn("w:sz"), str(size)); el.set(qn("w:space"), str(space)); el.set(qn("w:color"), color)
        bd.append(el)

    def add_link(par, url, label, style):
        rid = par.part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
        link = OxmlElement("w:hyperlink"); link.set(qn("r:id"), rid)
        r = OxmlElement("w:r"); rpr = OxmlElement("w:rPr")
        col = OxmlElement("w:color"); col.set(qn("w:val"), BRAND); rpr.append(col)
        u = OxmlElement("w:u"); u.set(qn("w:val"), "single"); rpr.append(u)
        if style.get("b"):
            rpr.append(OxmlElement("w:b"))
        r.append(rpr)
        t = OxmlElement("w:t"); t.text = label; t.set(qn("xml:space"), "preserve"); r.append(t)
        link.append(r); par._p.append(link)

    def add_runs(par, runs, size=None, color=None, bold=False):
        for txt, s in runs:
            if s.get("br"):
                par.add_run().add_break(); continue
            if s.get("link") and re.match(r"(?i)^(https?://|mailto:)", s["link"]):
                add_link(par, s["link"], txt, s); continue
            r = par.add_run(txt)
            if s.get("b") or bold:          # on ne pose l'attribut que s'il est vrai : « faux » écraserait le gras d'un titre
                r.bold = True
            if s.get("i"):
                r.italic = True
            if s.get("s"):
                r.font.strike = True
            if s.get("code"):
                r.font.name, r.font.size = "Consolas", Pt(9.5)
                shade(r._r.get_or_add_rPr(), "EEF3F7")
            elif size:
                r.font.size = Pt(size)
            if color is not None:
                r.font.color.rgb = color

    def emit(blocks, level=0, quote=False):
        for b in blocks:
            kind = b[0]
            if kind == "heading":
                add_runs(doc.add_heading("", level=min(b[1], 4)), b[2])
            elif kind == "para":
                p = doc.add_paragraph()
                if quote:
                    p.paragraph_format.left_indent = Cm(0.6); border(p, "left", BRAND, 18, 8)
                add_runs(p, b[1], color=RGBColor(0x5B, 0x6B, 0x7B) if quote else None)
            elif kind == "list":
                _, ordered, start, items = b
                for n, item in enumerate(items, start):
                    first = True
                    for sub in item:
                        if sub[0] == "para" and first:
                            p = doc.add_paragraph(style=None if ordered else f"List Bullet{'' if level == 0 else ' ' + str(min(level + 1, 3))}")
                            if ordered:   # numérotation écrite à la main : Word poursuivrait sinon la numérotation d'une liste à l'autre
                                p.paragraph_format.left_indent = Cm(0.9 + 0.7 * level)
                                p.paragraph_format.first_line_indent = Cm(-0.6)
                                p.add_run(f"{n}. ")
                            p.paragraph_format.space_after = Pt(2)
                            add_runs(p, sub[1]); first = False
                        else:
                            emit([sub], level + 1, quote)
            elif kind == "quote":
                emit(b[1], level, True)
            elif kind == "code":
                for line in (b[1].split("\n") or [""]):
                    p = doc.add_paragraph()
                    p.paragraph_format.space_after = Pt(0); p.paragraph_format.left_indent = Cm(0.3)
                    shade(p._p.get_or_add_pPr(), "F1F5F8")
                    r = p.add_run(line or " "); r.font.name, r.font.size = "Consolas", Pt(9)
                doc.add_paragraph().paragraph_format.space_after = Pt(2)
            elif kind == "hr":
                p = doc.add_paragraph(); border(p, "bottom")
            elif kind == "table":
                _, header, rows, aligns = b
                cols = max([len(header)] + [len(r) for r in rows] + [1])
                tbl = doc.add_table(rows=0, cols=cols)
                tbl.style = "Table Grid"; tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
                for ri, row in enumerate([header] + rows):
                    if ri == 0 and not header:
                        continue
                    cells = tbl.add_row().cells
                    for ci in range(cols):
                        runs = row[ci] if ci < len(row) else []
                        p = cells[ci].paragraphs[0]; p.paragraph_format.space_after = Pt(1)
                        al = aligns[ci] if ci < len(aligns) else "left"
                        p.alignment = {"right": WD_ALIGN_PARAGRAPH.RIGHT, "center": WD_ALIGN_PARAGRAPH.CENTER}.get(al, WD_ALIGN_PARAGRAPH.LEFT)
                        if ri == 0:
                            add_runs(p, runs, size=10, color=RGBColor(255, 255, 255), bold=True); shade(cells[ci]._tc.get_or_add_tcPr(), BRAND)
                        else:
                            add_runs(p, runs, size=10)
                            if ri % 2 == 0:
                                shade(cells[ci]._tc.get_or_add_tcPr(), "F3F7FA")
                    if ri == 0:
                        trPr = tbl.rows[0]._tr.get_or_add_trPr(); trPr.append(OxmlElement("w:tblHeader"))   # en-tête répété à chaque page
                doc.add_paragraph().paragraph_format.space_after = Pt(4)

    # En-tête : logo ; titre et date
    hp = sec.header.paragraphs[0]
    if _logo():
        hp.add_run().add_picture(str(_logo()), width=Cm(3.6))
        border(hp, "bottom", "C9D6E2", 6, 6)
    if show_title:
        t = doc.add_paragraph(); t.paragraph_format.space_after = Pt(0)
        r = t.add_run(title); r.bold = True; r.font.size, r.font.color.rgb = Pt(22), blue
        d = doc.add_paragraph(); d.paragraph_format.space_after = Pt(10)
        r = d.add_run(_today()); r.font.size, r.font.color.rgb = Pt(9.5), RGBColor(0x7A, 0x8C, 0x99)
    emit(blocks)

    # Pied de page : mention et numéro de page
    numbers = settings.app().export_page_numbers
    if _footer() or numbers:
        fp = sec.footer.paragraphs[0]; fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        text = _footer() + ("   ·   " if _footer() and numbers else "") + ("Page " if numbers else "")
        r = fp.add_run(text); r.font.size, r.font.color.rgb = Pt(8), RGBColor(0x7A, 0x8C, 0x99)
        if numbers:
            r2 = fp.add_run(); r2.font.size, r2.font.color.rgb = Pt(8), RGBColor(0x7A, 0x8C, 0x99)
            for kind, txt in (("begin", None), (None, "PAGE"), ("end", None)):
                if kind:
                    el = OxmlElement("w:fldChar"); el.set(qn("w:fldCharType"), kind)
                else:
                    el = OxmlElement("w:instrText"); el.set(qn("xml:space"), "preserve"); el.text = txt
                r2._r.append(el)

    doc.core_properties.title, doc.core_properties.author = title, "Assistant Opti"
    buf = io.BytesIO(); doc.save(buf)
    return buf.getvalue()


# ═════════════════════════════════════════════════════════════════════════════
# PDF
# ═════════════════════════════════════════════════════════════════════════════
def _fonts() -> tuple[str, str, str, str, str]:
    """(normal, gras, italique, gras-italique, monospace) : DejaVu si présent (accents, €, guillemets…), sinon Helvetica."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for base in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF"):
        d = Path(base)
        files = {k: d / f for k, f in (("n", "DejaVuSans.ttf"), ("b", "DejaVuSans-Bold.ttf"),
                                       ("i", "DejaVuSans-Oblique.ttf"), ("bi", "DejaVuSans-BoldOblique.ttf"), ("m", "DejaVuSansMono.ttf"))}
        # normal, gras et mono suffisent (paquet fonts-dejavu-core) ; sans les variantes italiques, l'italique reste droit
        if all(files[k].exists() for k in ("n", "b", "m")):
            if "DejaVuSans" not in pdfmetrics.getRegisteredFontNames():
                for name, key, fallback in (("DejaVuSans", "n", "n"), ("DejaVuSans-Bold", "b", "b"), ("DejaVuSans-Oblique", "i", "n"),
                                            ("DejaVuSans-BoldOblique", "bi", "b"), ("DejaVuSansMono", "m", "m")):
                    pdfmetrics.registerFont(TTFont(name, str(files[key] if files[key].exists() else files[fallback])))
                pdfmetrics.registerFontFamily("DejaVuSans", normal="DejaVuSans", bold="DejaVuSans-Bold",
                                              italic="DejaVuSans-Oblique", boldItalic="DejaVuSans-BoldOblique")
            return "DejaVuSans", "DejaVuSans-Bold", "DejaVuSans-Oblique", "DejaVuSans-BoldOblique", "DejaVuSansMono"
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique", "Courier"


def to_pdf(text: str, title: str, delimited: bool = False) -> bytes:
    import textwrap

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (HRFlowable, ListFlowable, ListItem, Paragraph, Preformatted, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    title, blocks, show_title = _head(parse(text), title or "Document", delimited)
    normal, bold, ital, boldital, mono = _fonts()
    blue, grey = colors.HexColor("#" + BRAND), colors.HexColor("#5b6b7b")
    S = {
        "p": ParagraphStyle("p", fontName=normal, fontSize=10, leading=14.5, spaceAfter=6, textColor=colors.HexColor("#17293a")),
        "title": ParagraphStyle("title", fontName=bold, fontSize=20, leading=25, textColor=blue, spaceAfter=2),
        "date": ParagraphStyle("date", fontName=normal, fontSize=8.5, textColor=colors.HexColor("#7a8c99"), spaceAfter=12),
        "code": ParagraphStyle("code", fontName=mono, fontSize=8, leading=10.5, backColor=colors.HexColor("#f1f5f8"),
                               leftIndent=6, rightIndent=6, borderPadding=5, spaceAfter=8, spaceBefore=2),
        "th": ParagraphStyle("th", fontName=bold, fontSize=9, leading=12, textColor=colors.white),
        "td": ParagraphStyle("td", fontName=normal, fontSize=9, leading=12),
        "quote": ParagraphStyle("quote", fontName=ital, fontSize=10, leading=14.5, leftIndent=12, textColor=grey, spaceAfter=6),
    }
    for n, size in ((1, 15), (2, 12.5), (3, 11), (4, 10)):
        S[f"h{n}"] = ParagraphStyle(f"h{n}", fontName=bold, fontSize=size, leading=size + 4, textColor=blue, spaceBefore=12, spaceAfter=4, keepWithNext=1)

    def markup(runs) -> str:
        out = []
        for txt, s in runs:
            if s.get("br"):
                out.append("<br/>"); continue
            t = escape(txt)
            if s.get("code"):
                t = f'<font face="{mono}" backColor="#eef3f7">{t}</font>'
            if s.get("b"):
                t = f"<b>{t}</b>"
            if s.get("i"):
                t = f"<i>{t}</i>"
            if s.get("s"):
                t = f"<strike>{t}</strike>"
            if s.get("link") and re.match(r"(?i)^(https?://|mailto:)", s["link"]):
                t = f'<a href="{escape(s["link"], {chr(34): "&quot;"})}" color="#{BRAND}"><u>{t}</u></a>'
            out.append(t)
        return "".join(out) or " "

    width = A4[0] - 4.4 * cm

    def emit(blocks, quote=False):
        flow = []
        for b in blocks:
            kind = b[0]
            if kind == "heading":
                flow.append(Paragraph(markup(b[2]), S[f"h{min(b[1], 4)}"]))
            elif kind == "para":
                flow.append(Paragraph(markup(b[1]), S["quote"] if quote else S["p"]))
            elif kind == "list":
                _, ordered, start, items = b
                lis = [ListItem(emit(item, quote) or [Spacer(1, 1)], leftIndent=16) for item in items]
                opts = {"bulletType": "1", "start": start, "bulletFormat": "%s."} if ordered else {"bulletType": "bullet", "start": "•"}
                flow.append(ListFlowable(lis, bulletFontName=normal, bulletFontSize=9.5, leftIndent=16, bulletColor=blue, **opts))
            elif kind == "quote":
                flow += emit(b[1], True)
            elif kind == "code":
                lines = []
                for line in b[1].split("\n"):
                    lines += textwrap.wrap(line, 98, drop_whitespace=False, replace_whitespace=False) or [""]
                flow.append(Preformatted("\n".join(lines), S["code"]))
            elif kind == "hr":
                flow.append(HRFlowable(width="100%", color=colors.HexColor("#c9d6e2"), spaceBefore=4, spaceAfter=8))
            elif kind == "table":
                _, header, rows, aligns = b
                cols = max([len(header)] + [len(r) for r in rows] + [1])
                def cell(runs, ci, head=False):
                    al = aligns[ci] if ci < len(aligns) else "left"
                    st = ParagraphStyle("c", parent=S["th" if head else "td"], alignment={"right": 2, "center": 1}.get(al, 0))
                    return Paragraph(markup(runs), st)
                data = ([[cell(header[ci] if ci < len(header) else [], ci, True) for ci in range(cols)]] if header else []) + \
                       [[cell(r[ci] if ci < len(r) else [], ci) for ci in range(cols)] for r in rows]
                lens = [max([len(plain(header[ci])) if ci < len(header) else 0] + [len(plain(r[ci])) if ci < len(r) else 0 for r in rows] + [4]) for ci in range(cols)]
                weights = [min(max(l, 6), 40) for l in lens]
                tw = [width * w / sum(weights) for w in weights]
                t = Table(data, colWidths=tw, repeatRows=1 if header else 0)
                style = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d6e2")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                         ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                         ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]
                if header:
                    style.append(("BACKGROUND", (0, 0), (-1, 0), blue))
                for r in range(2 if header else 1, len(data), 2):
                    style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#f3f7fa")))
                t.setStyle(TableStyle(style))
                flow += [t, Spacer(1, 8)]
        return flow

    def page(canvas, doc):
        canvas.saveState()
        if _logo():
            canvas.drawImage(str(_logo()), 2.2 * cm, A4[1] - 1.95 * cm, width=3.2 * cm, height=3.2 * cm * 285 / 852, mask="auto")
            canvas.setStrokeColor(colors.HexColor("#c9d6e2")); canvas.setLineWidth(0.5)
            canvas.line(2.2 * cm, A4[1] - 2.15 * cm, A4[0] - 2.2 * cm, A4[1] - 2.15 * cm)
        line = _footer_line(str(doc.page))
        if line:
            canvas.setFont(normal, 7.5); canvas.setFillColor(colors.HexColor("#7a8c99"))
            canvas.drawCentredString(A4[0] / 2, 1.2 * cm, line)
        canvas.restoreState()

    buf = io.BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2.2 * cm, rightMargin=2.2 * cm, topMargin=2.7 * cm, bottomMargin=2.0 * cm,
                            title=title, author="Assistant Opti")
    head = [Paragraph(escape(title), S["title"]), Paragraph(_today(), S["date"])] if show_title else []
    pdf.build(head + emit(blocks), onFirstPage=page, onLaterPages=page)
    return buf.getvalue()


# ═════════════════════════════════════════════════════════════════════════════
# EXCEL
# ═════════════════════════════════════════════════════════════════════════════
_NUM = re.compile(r"^-?(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d+)?$")


def _cell_value(s: str):
    """Nombre si la cellule en est un (formats français compris), sinon texte. Jamais de formule."""
    s = s.strip()
    pct = s.endswith("%")
    core = s[:-1].strip() if pct else s
    core = core[:-1].strip() if core.endswith("€") else core
    if _NUM.match(core):
        digits = re.sub(r"\D", "", core)
        if not (len(digits) > 1 and digits.startswith("0") and "," not in core and "." not in core) and len(digits) <= 15:
            num = float(re.sub(r"[ \u00a0\u202f]", "", core).replace(",", "."))
            if pct:
                return num / 100, "0.0%"
            return (int(num) if num.is_integer() and not re.search(r"[.,]", core) else num), None
    return s, None


def to_xlsx(text: str, title: str, delimited: bool = False) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    blocks = parse(text)
    tables, current = [], ""
    def scan(bl):
        nonlocal current
        for b in bl:
            if b[0] == "heading":
                current = plain(b[2]).strip()
            elif b[0] == "table":
                tables.append((current, b))
            elif b[0] == "list":
                for item in b[3]:
                    scan(item)
            elif b[0] == "quote":
                scan(b[1])
    scan(blocks)
    if not tables:
        raise NoTable()

    wb = Workbook(); wb.remove(wb.active)
    used = set()
    for n, (heading, (_, header, rows, aligns)) in enumerate(tables, 1):
        name = re.sub(r"[\[\]:*?/\\]", " ", heading)[:28].strip() or f"Tableau {n}"
        while name.lower() in used:
            name = f"{name[:25]} {n}"
        used.add(name.lower())
        ws = wb.create_sheet(name)
        cols = max([len(header)] + [len(r) for r in rows] + [1])
        allrows = ([header] if header else []) + rows
        for ri, row in enumerate(allrows, 1):
            for ci in range(cols):
                raw = plain(row[ci]) if ci < len(row) else ""
                c = ws.cell(row=ri, column=ci + 1)
                if header and ri == 1:
                    c.value = raw
                    c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor=BRAND)
                    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                else:
                    val, fmt = _cell_value(raw)
                    c.value = val
                    if isinstance(val, str):
                        c.data_type = "s"      # un texte commençant par « = » ne doit jamais devenir une formule
                        c.alignment = Alignment(vertical="top", wrap_text=len(val) > 60)
                    elif fmt:
                        c.number_format = fmt
        for ci in range(cols):
            width = max([len(plain(header[ci])) if header and ci < len(header) else 0] +
                        [len(plain(r[ci])) if ci < len(r) else 0 for r in rows] + [8])
            ws.column_dimensions[get_column_letter(ci + 1)].width = min(width + 3, 60)
        if header:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = f"A1:{get_column_letter(cols)}{len(allrows)}"
    wb.properties.title, wb.properties.creator = title, "Assistant Opti"
    buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


FORMATS = {
    "docx": (to_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "pdf": (to_pdf, "application/pdf"),
    "xlsx": (to_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
}


def build(fmt: str, content: str, title: str) -> tuple[bytes, str]:
    """Met en forme `content` (la réponse du modèle) : uniquement le document si le modèle l'a délimité."""
    text, delimited = prepare(content)
    if not text:
        raise ValueError("vide")
    fn, media = FORMATS[fmt]
    return fn(text, title, delimited), media
