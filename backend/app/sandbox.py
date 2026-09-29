"""
Bac à sable d'exécution du code d'analyse écrit par le modèle.

Mode docker (production) : un conteneur jetable par exécution, sans réseau,
système de fichiers en lecture seule, utilisateur non privilégié, mémoire,
CPU, nombre de processus et durée limités. Les fichiers de la conversation
sont montés en lecture seule dans /data.

Mode subprocess (développement) : simple processus limité en mémoire et en
temps. Il n'isole PAS du reste de la machine : ne jamais l'utiliser en production.
"""
import asyncio
import json
import logging
import os
import resource
import sys
import uuid
from pathlib import Path

from . import config, settings

log = logging.getLogger("opti.sandbox")
OUTPUT_LIMIT = 12_000   # caractères de sortie renvoyés au modèle


def _truncate(text: str, limit: int | None = OUTPUT_LIMIT) -> str:
    if limit is None or len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n[… sortie tronquée …]\n" + text[-half:]


async def run_code(code: str, files: list[tuple[Path, str]], *, timeout: int | None = None,
                   output_limit: int | None = OUTPUT_LIMIT) -> tuple[bool, str]:
    """
    Exécute `code`. files = [(chemin sur le serveur, nom dans /data)]. Renvoie (succès, sortie).
    timeout : durée maximale en secondes (par défaut celle des réglages).
    output_limit : nombre de caractères de sortie conservés (None = tout).
    """
    timeout = timeout or settings.app().sandbox_timeout
    if config.SANDBOX_MODE == "docker":
        return await _run_docker(code, files, timeout, output_limit)
    return await _run_subprocess(code, files, timeout, output_limit)


async def _communicate(proc, code: str, on_timeout, timeout: int, output_limit) -> tuple[bool, str]:
    try:
        out, _ = await asyncio.wait_for(proc.communicate(code.encode()), timeout=timeout + 5)
    except asyncio.TimeoutError:
        await on_timeout()
        return False, f"Erreur : l'exécution a dépassé {timeout} secondes et a été arrêtée."
    text = out.decode(errors="replace").strip() or "(aucune sortie : utilise print() pour afficher les résultats)"
    return proc.returncode == 0, _truncate(text, output_limit)


async def _run_docker(code: str, files: list[tuple[Path, str]], timeout: int, output_limit) -> tuple[bool, str]:
    name = f"opti-sbx-{uuid.uuid4().hex[:12]}"
    cmd = [
        "docker", "run", "--rm", "-i", "--name", name,
        "--network", "none",
        "--read-only", "--tmpfs", "/tmp:rw,size=256m,mode=1777",
        "--memory", config.SANDBOX_MEMORY, "--memory-swap", config.SANDBOX_MEMORY,
        "--cpus", "2", "--pids-limit", "64",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "10001:10001",
    ]
    for host_path, inner in files:
        cmd += ["-v", f"{host_path}:/data/{inner}:ro"]
    cmd += [config.SANDBOX_IMAGE, "timeout", "-s", "KILL", str(timeout), "python", "-I", "-"]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)

    async def kill():
        k = await asyncio.create_subprocess_exec("docker", "kill", name,
                                                 stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await k.wait()

    ok, out = await _communicate(proc, code, kill, timeout, output_limit)
    if proc.returncode == 137:
        return False, "Erreur : l'exécution a été arrêtée (limite de temps ou de mémoire atteinte)."
    if proc.returncode == 125:
        log.error("Docker n'a pas pu lancer le bac à sable : %s", out)
        return False, "Erreur interne : le bac à sable d'analyse est indisponible."
    return ok, out


async def _run_subprocess(code: str, files: list[tuple[Path, str]], timeout: int, output_limit) -> tuple[bool, str]:
    workdir = config.DATA_DIR / "sandbox-dev" / uuid.uuid4().hex[:12]
    data_dir = workdir / "data"
    data_dir.mkdir(parents=True)
    for host_path, inner in files:
        os.symlink(host_path, data_dir / inner)
    # En mode développement, les fichiers sont dans <workdir>/data et non /data
    code = code.replace("/data/", f"{data_dir}/")

    def limits():
        mem = 2 * 1024 ** 3
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout))

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-", cwd=workdir, env={"PATH": "/usr/bin:/bin"}, preexec_fn=limits,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)

    async def kill():
        proc.kill()

    try:
        ok, out = await _communicate(proc, code, kill, timeout, output_limit)
        return ok, out.replace(f"{data_dir}/", "/data/")
    finally:
        for p in sorted(workdir.rglob("*"), reverse=True):
            p.unlink() if p.is_file() or p.is_symlink() else p.rmdir()
        workdir.rmdir()


# ── Profil d'un fichier de données (code fixe, exécuté lui aussi dans le bac à sable) ──
PROFILE_CODE = r'''
import pandas as pd
pd.set_option("display.width", 200); pd.set_option("display.max_columns", 40)
path = "/data/{name}"
def show(df, label):
    print(f"{{label}}{{len(df)}} lignes x {{len(df.columns)}} colonnes")
    print("Colonnes et types :")
    for c in df.columns:
        print(f"  - {{c}} : {{df[c].dtype}} ({{df[c].isna().sum()}} vides)")
    print("Premières lignes :")
    print(df.head(5).to_string(max_colwidth=40))
if path.lower().endswith((".xlsx", ".xlsm")):
    sheets = pd.read_excel(path, sheet_name=None)
    print(f"Classeur Excel, {{len(sheets)}} feuille(s) : {{', '.join(sheets)}}")
    print(f'Pour lire ce fichier : pd.read_excel("{{path}}", sheet_name="<nom de la feuille>")')
    for s, df in list(sheets.items())[:5]:
        print(); show(df, f"Feuille « {{s}} » : ")
else:
    best = None
    for enc in ("utf-8-sig", "latin-1"):
        for sep in (",", ";", "\t", "|"):
            try:
                df = pd.read_csv(path, sep=sep, encoding=enc, low_memory=False)
            except Exception:
                continue
            if best is None or len(df.columns) > len(best[0].columns):
                best = (df, sep, enc)
        if best and len(best[0].columns) > 1:
            break
    if best is None:
        raise SystemExit("Impossible de lire ce CSV.")
    df, sep, enc = best
    print(f'Pour lire ce fichier : pd.read_csv("{{path}}", sep={{sep!r}}, encoding={{enc!r}}, low_memory=False)')
    show(df, "")
'''


async def profile_data_file(host_path: Path, stored_name: str) -> str:
    ok, out = await run_code(PROFILE_CODE.format(name=stored_name), [(host_path, stored_name)])
    return out if ok else f"Profil indisponible : {out[-500:]}"



# ── Lecture d'un PDF (texte, tableaux, OCR des pages scannées) ────────────────
# Le PDF n'est jamais ouvert par l'application elle-même : tout se passe dans le
# bac à sable (sans réseau, lecture seule), car un PDF malveillant peut exploiter
# les failles de ses lecteurs.
PDF_EXTRACT_BODY = r"""
import glob, json, logging, os, re, shutil, subprocess, tempfile
logging.disable(logging.CRITICAL)
import pdfplumber

PATH = "/data/" + PARAMS["name"]
MIN_TEXT = 40

def md_table(rows):
    rows = [[(c or "").replace("\n", " ").replace("|", "/").strip() for c in r] for r in rows]
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)

def good_table(rows):
    if len(rows) < 2 or max(len(r) for r in rows) < 2:
        return False
    cells = [c for r in rows for c in r]
    return sum(1 for c in cells if c and str(c).strip()) >= 0.4 * len(cells)

pages, weak = [], []
with pdfplumber.open(PATH) as pdf:
    total = len(pdf.pages)
    for i, page in enumerate(pdf.pages[:PARAMS["max_pages"]], 1):
        text, tables = "", []
        try:
            keep = []
            for t in page.find_tables():
                rows = t.extract()
                if good_table(rows):
                    keep.append((t, rows))
            outside = page
            for t, _ in keep:
                outside = outside.outside_bbox(t.bbox)
            text = (outside.extract_text() or "").strip()
            tables = [md_table(rows) for _, rows in keep]
        except Exception:
            try:
                text = (page.extract_text() or "").strip()
            except Exception:
                text = ""
        garbage = text.count("(cid:") >= 5           # police sans table de caractères : texte illisible
        text = re.sub(r"\(cid:\d+\)", "", text).strip()
        body = text
        if tables:
            body += ("\n\n" if body else "") + "\n\n".join("[Tableau]\n" + t for t in tables)
        pages.append({"n": i, "text": body, "ocr": False, "tables": len(tables)})
        img_area = 0
        try:
            for im in page.images:
                w = max(0, min(im["x1"], page.width) - max(im["x0"], 0))
                h = max(0, min(im["bottom"], page.height) - max(im["top"], 0))
                img_area += w * h
        except Exception:
            pass
        mostly_image = img_area / ((page.width * page.height) or 1) > 0.5
        if garbage or (len(body) < MIN_TEXT and len(page.images) > 0) or (mostly_image and len(body) < 300):
            weak.append(i)          # scan probable, texte illisible ou page-image avec un simple en-tête
        page.flush_cache()

ocr_available = bool(shutil.which("tesseract") and shutil.which("pdftoppm"))
ocr_done = 0
if PARAMS["ocr"] and weak and ocr_available:
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    for n in weak[:PARAMS["max_ocr"]]:
        txt = ""
        with tempfile.TemporaryDirectory() as tmp:
            try:
                subprocess.run(["pdftoppm", "-r", "200", "-f", str(n), "-l", str(n), "-png", PATH, tmp + "/p"],
                               capture_output=True, timeout=60, check=True)
                img = sorted(glob.glob(tmp + "/p*.png"))[0]
                r = subprocess.run(["tesseract", img, "stdout", "-l", "fra+eng"],
                                   capture_output=True, timeout=120, env=env)
                txt = r.stdout.decode("utf-8", "replace").strip()
            except Exception:
                txt = ""
        old = pages[n - 1]["text"]
        if txt and (len(txt) >= 0.8 * len(old) or len(old) < MIN_TEXT):
            pages[n - 1]["text"] = txt
            pages[n - 1]["ocr"] = True
            ocr_done += 1
unread = [n for n in weak if not pages[n - 1]["ocr"] and len(pages[n - 1]["text"]) < 100]

print("@@RESULT@@" + json.dumps({
    "total": total, "pages": pages, "weak": weak, "ocr_done": ocr_done, "unread": len(unread),
    "ocr_available": ocr_available, "truncated": total > PARAMS["max_pages"],
}, ensure_ascii=False))
"""


async def extract_pdf(host_path: Path, stored_name: str, *, ocr: bool, max_ocr: int,
                      max_pages: int, timeout: int) -> dict:
    """Renvoie {total, pages:[{n, text, ocr, tables}], weak, ocr_done, ocr_available, truncated}."""
    params = {"name": stored_name, "ocr": ocr, "max_ocr": max_ocr, "max_pages": max_pages}
    code = "import json as _j\nPARAMS = _j.loads(" + repr(json.dumps(params)) + ")\n" + PDF_EXTRACT_BODY
    ok, out = await run_code(code, [(host_path, stored_name)], timeout=timeout, output_limit=None)
    marker = out.rfind("@@RESULT@@")
    if not ok or marker < 0:
        raise RuntimeError(out[-400:])
    return json.loads(out[marker + len("@@RESULT@@"):])
