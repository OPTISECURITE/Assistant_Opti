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


# ── Fichiers produits par le code (graphiques, exports) ──────────────────────────────────────────────────────
# Le code écrit dans /out ; à la fin, les fichiers ressortent par la sortie standard (base64) : le conteneur n'a ni réseau,
# ni disque inscriptible partagé avec le serveur. Tout est REVALIDÉ ici, car ce qui sort du conteneur n'est pas digne de confiance.
OUTPUT_TYPES = {                       # extension : (type MIME, début attendu du contenu)
    ".png": ("image/png", b"\x89PNG"),
    ".jpg": ("image/jpeg", b"\xff\xd8"),
    ".jpeg": ("image/jpeg", b"\xff\xd8"),
    ".pdf": ("application/pdf", b"%PDF"),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"PK"),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"PK"),
    ".csv": ("text/csv; charset=utf-8", None),
    ".txt": ("text/plain; charset=utf-8", None),
}
IMAGE_EXT = {".png", ".jpg", ".jpeg"}
MAX_OUT_FILES, MAX_OUT_FILE, MAX_OUT_TOTAL = 10, 15_000_000, 30_000_000

OUTPUT_WRAPPER = r"""
import base64, os, sys, traceback
_CODE = __USER_CODE__
_OUT = __OUT_DIR__
_EXT = __EXTENSIONS__
_failed = False
try:
    exec(compile(_CODE, "<analyse>", "exec"), {"__name__": "__main__"})
except SystemExit:
    pass
except BaseException:
    _failed = True
    _t, _v, _tb = sys.exc_info()
    traceback.print_exception(_t, _v, _tb.tb_next)          # sans la ligne de ce lanceur
sys.stdout.write("\n"); sys.stdout.flush()
try:
    for _name in sorted(os.listdir(_OUT))[:50]:
        _p = os.path.join(_OUT, _name)
        if os.path.islink(_p) or not os.path.isfile(_p):
            print("@@SKIP@@" + _name + " (lien ou dossier ignoré)"); continue
        if os.path.splitext(_name)[1].lower() not in _EXT:
            print("@@SKIP@@" + _name + " (type non autorisé)"); continue
        if os.path.getsize(_p) > 15000000:
            print("@@SKIP@@" + _name + " (trop volumineux)"); continue
        with open(_p, "rb") as _f:
            print("@@FILE@@" + base64.b64encode(_name.encode()).decode() + "@@" + base64.b64encode(_f.read()).decode())
except Exception as _e:
    print("@@SKIP@@lecture des fichiers impossible : " + str(_e))
sys.exit(1 if _failed else 0)
"""


def validate_outputs(raw: list[tuple[str, bytes]]) -> tuple[list[tuple[str, bytes]], list[str]]:
    """Filtre ce qui est sorti du conteneur : extension autorisée, contenu conforme, tailles, nombre."""
    kept, skipped, total = [], [], 0
    for name, data in raw:
        base = os.path.basename(name.replace("\\", "/")).strip()[:120]
        ext = os.path.splitext(base)[1].lower()
        if not base or ext not in OUTPUT_TYPES:
            skipped.append(f"{base or '?'} (type non autorisé)")
        elif not data or len(data) > MAX_OUT_FILE:
            skipped.append(f"{base} (vide ou trop volumineux)")
        elif OUTPUT_TYPES[ext][1] and not data.startswith(OUTPUT_TYPES[ext][1]):
            skipped.append(f"{base} (contenu invalide)")
        elif len(kept) >= MAX_OUT_FILES or total + len(data) > MAX_OUT_TOTAL:
            skipped.append(f"{base} (limite de fichiers atteinte)")
        else:
            kept.append((base, data))
            total += len(data)
    return kept, skipped


def _parse_outputs(text: str) -> tuple[str, list[tuple[str, bytes]], list[str]]:
    import base64
    lines, raw, skipped = [], [], []
    for line in text.split("\n"):
        if line.startswith("@@FILE@@"):
            try:
                name_b64, data_b64 = line[8:].split("@@", 1)
                raw.append((base64.b64decode(name_b64).decode("utf-8", "replace"), base64.b64decode(data_b64)))
            except Exception:
                skipped.append("un fichier illisible")
        elif line.startswith("@@SKIP@@"):
            skipped.append(line[8:][:200])
        else:
            lines.append(line)
    kept, rejected = validate_outputs(raw)
    return "\n".join(lines), kept, skipped + rejected


class _TooBig(Exception):
    pass


async def _pump(proc, code: bytes, timeout: int, cap: int) -> bytes:
    """Envoie le code et lit la sortie, bornée à `cap` octets. Lève TimeoutError ou _TooBig."""
    async def go() -> bytes:
        try:
            proc.stdin.write(code)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass                                            # le processus s'est arrêté avant d'avoir tout lu
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
        chunks, total = [], 0
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > cap:
                raise _TooBig()
            chunks.append(chunk)
        await proc.wait()
        return b"".join(chunks)
    return await asyncio.wait_for(go(), timeout + 5)


async def _execute(code: str, files: list[tuple[Path, str]], timeout: int, max_bytes: int,
                   with_out: bool = False) -> tuple[int | None, bytes, str | None]:
    """Exécute le code dans le bac à sable. Renvoie (code de retour, sortie brute, message d'erreur éventuel)."""
    if config.SANDBOX_MODE == "docker":
        return await _exec_docker(code, files, timeout, max_bytes, with_out)
    return await _exec_subprocess(code, files, timeout, max_bytes, with_out)


def _wrap(code: str, out_dir: str) -> str:
    return (OUTPUT_WRAPPER.replace("__USER_CODE__", repr(code)).replace("__OUT_DIR__", repr(out_dir))
            .replace("__EXTENSIONS__", repr(sorted(OUTPUT_TYPES))))


async def _exec_docker(code, files, timeout, max_bytes, with_out):
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
    if with_out:
        cmd += ["--tmpfs", "/out:rw,size=64m,mode=1777"]
    for host_path, inner in files:
        cmd += ["-v", f"{host_path}:/data/{inner}:ro"]
    cmd += [config.SANDBOX_IMAGE, "timeout", "-s", "KILL", str(timeout), "python", "-I", "-"]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except OSError as e:                                    # docker absent ou inaccessible
        log.error("Docker introuvable ou inaccessible : %s", e)
        return None, b"", "Erreur interne : le bac à sable d'analyse est indisponible."

    async def kill():
        k = await asyncio.create_subprocess_exec("docker", "kill", name,
                                                 stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await k.wait()

    script = _wrap(code, "/out") if with_out else code
    try:
        raw = await _pump(proc, script.encode(), timeout, max_bytes)
    except asyncio.TimeoutError:
        await kill()
        return None, b"", f"Erreur : l'exécution a dépassé {timeout} secondes et a été arrêtée."
    except _TooBig:
        await kill()
        return None, b"", f"Erreur : la sortie du programme dépasse {max_bytes // 1_000_000} Mo : elle a été arrêtée."
    if proc.returncode == 137:
        return proc.returncode, raw, "Erreur : l'exécution a été arrêtée (limite de temps ou de mémoire atteinte)."
    if proc.returncode == 125:
        log.error("Docker n'a pas pu lancer le bac à sable : %s", raw[-500:].decode(errors="replace"))
        return proc.returncode, raw, "Erreur interne : le bac à sable d'analyse est indisponible."
    return proc.returncode, raw, None


async def _exec_subprocess(code, files, timeout, max_bytes, with_out):
    workdir = config.DATA_DIR / "sandbox-dev" / uuid.uuid4().hex[:12]
    data_dir, out_dir = workdir / "data", workdir / "out"
    data_dir.mkdir(parents=True)
    out_dir.mkdir()
    for host_path, inner in files:
        os.symlink(host_path, data_dir / inner)
    # En mode développement, les dossiers sont dans <workdir> et non à la racine
    code = code.replace("/data/", f"{data_dir}/").replace("/out/", f"{out_dir}/")
    script = _wrap(code, str(out_dir)) if with_out else code

    def limits():
        mem = 2 * 1024 ** 3
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout))

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-", cwd=workdir, env={"PATH": "/usr/bin:/bin", "MPLBACKEND": "Agg", "MPLCONFIGDIR": str(workdir)},
        preexec_fn=limits, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        raw = await _pump(proc, script.encode(), timeout, max_bytes)
    except asyncio.TimeoutError:
        proc.kill()
        return None, b"", f"Erreur : l'exécution a dépassé {timeout} secondes et a été arrêtée."
    except _TooBig:
        proc.kill()
        return None, b"", f"Erreur : la sortie du programme dépasse {max_bytes // 1_000_000} Mo : elle a été arrêtée."
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)
    if proc.returncode is not None and proc.returncode < 0:          # tué par un signal (limite de temps processeur, mémoire…)
        return proc.returncode, raw, "Erreur : l'exécution a été arrêtée (limite de temps ou de mémoire atteinte)."
    return proc.returncode, raw.replace(f"{data_dir}/".encode(), b"/data/").replace(f"{out_dir}/".encode(), b"/out/"), None


async def run_code(code: str, files: list[tuple[Path, str]], *, timeout: int | None = None,
                   output_limit: int | None = OUTPUT_LIMIT, max_bytes: int | None = None) -> tuple[bool, str]:
    """
    Exécute `code`. files = [(chemin sur le serveur, nom dans /data)]. Renvoie (succès, sortie).
    timeout : durée maximale en secondes (par défaut celle des réglages).
    output_limit : nombre de caractères de sortie conservés (None = tout).
    max_bytes : sortie maximale lue avant d'arrêter le programme.
    """
    timeout = timeout or settings.app().sandbox_timeout
    cap = max_bytes or (5_000_000 if output_limit is not None else 30_000_000)
    returncode, raw, error = await _execute(code, files, timeout, cap)
    if error:
        return False, error
    text = raw.decode(errors="replace").strip() or "(aucune sortie : utilise print() pour afficher les résultats)"
    return returncode == 0, _truncate(text, output_limit)


async def run_analysis(code: str, files: list[tuple[Path, str]]) -> tuple[bool, str, list[tuple[str, bytes]]]:
    """Comme run_code, avec un dossier /out où le code peut créer des graphiques et des fichiers : (succès, sortie, fichiers)."""
    timeout = settings.app().sandbox_timeout
    returncode, raw, error = await _execute(code, files, timeout, 45_000_000, with_out=True)
    if error:
        return False, error, []
    text, produced, skipped = _parse_outputs(raw.decode(errors="replace"))
    text = text.strip()
    if not text:
        text = "(aucun affichage)" if produced else "(aucune sortie : utilise print() pour afficher les résultats)"
    if skipped:
        text += "\nFichiers ignorés : " + " ; ".join(skipped)
    return returncode == 0, _truncate(text), produced


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
