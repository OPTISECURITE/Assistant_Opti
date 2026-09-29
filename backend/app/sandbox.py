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
import logging
import os
import resource
import sys
import uuid
from pathlib import Path

from . import config

log = logging.getLogger("opti.sandbox")
OUTPUT_LIMIT = 12_000   # caractères de sortie renvoyés au modèle


def _truncate(text: str) -> str:
    if len(text) <= OUTPUT_LIMIT:
        return text
    half = OUTPUT_LIMIT // 2
    return text[:half] + "\n[… sortie tronquée …]\n" + text[-half:]


async def run_code(code: str, files: list[tuple[Path, str]]) -> tuple[bool, str]:
    """Exécute `code`. files = [(chemin sur le serveur, nom dans /data)]. Renvoie (succès, sortie)."""
    if config.SANDBOX_MODE == "docker":
        return await _run_docker(code, files)
    return await _run_subprocess(code, files)


async def _communicate(proc, code: str, on_timeout) -> tuple[bool, str]:
    try:
        out, _ = await asyncio.wait_for(proc.communicate(code.encode()), timeout=config.SANDBOX_TIMEOUT + 5)
    except asyncio.TimeoutError:
        await on_timeout()
        return False, f"Erreur : l'exécution a dépassé {config.SANDBOX_TIMEOUT} secondes et a été arrêtée."
    text = out.decode(errors="replace").strip() or "(aucune sortie : utilise print() pour afficher les résultats)"
    return proc.returncode == 0, _truncate(text)


async def _run_docker(code: str, files: list[tuple[Path, str]]) -> tuple[bool, str]:
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
    cmd += [config.SANDBOX_IMAGE, "timeout", "-s", "KILL", str(config.SANDBOX_TIMEOUT), "python", "-I", "-"]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)

    async def kill():
        k = await asyncio.create_subprocess_exec("docker", "kill", name,
                                                 stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await k.wait()

    ok, out = await _communicate(proc, code, kill)
    if proc.returncode == 137:
        return False, "Erreur : l'exécution a été arrêtée (limite de temps ou de mémoire atteinte)."
    if proc.returncode == 125:
        log.error("Docker n'a pas pu lancer le bac à sable : %s", out)
        return False, "Erreur interne : le bac à sable d'analyse est indisponible."
    return ok, out


async def _run_subprocess(code: str, files: list[tuple[Path, str]]) -> tuple[bool, str]:
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
        resource.setrlimit(resource.RLIMIT_CPU, (config.SANDBOX_TIMEOUT, config.SANDBOX_TIMEOUT))

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-", cwd=workdir, env={"PATH": "/usr/bin:/bin"}, preexec_fn=limits,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)

    async def kill():
        proc.kill()

    try:
        ok, out = await _communicate(proc, code, kill)
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
