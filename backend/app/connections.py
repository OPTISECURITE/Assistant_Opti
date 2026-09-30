"""
Connexions API : l'assistant consulte des systèmes internes (Wipsos…) en LECTURE SEULE.

Une connexion = une adresse + une authentification + des OPÉRATIONS que l'administrateur a décrites (ou importées d'une
spécification OpenAPI) et activées une à une. Chaque opération activée devient un outil que le modèle peut appeler ; le serveur
exécute la requête, renvoie le résultat au modèle, qui rédige sa réponse à partir de ces données réelles.

Garde-fous : GET uniquement ; le modèle ne fournit que les valeurs des paramètres déclarés (validées, encodées) et ne peut viser
que l'adresse configurée ; pas de redirection suivie ; taille et durée limitées ; secrets chiffrés ; accès par utilisateur ;
chaque appel est journalisé ; débit limité par utilisateur.
"""
import json
import logging
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx
import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit, config, vault
from .models import ApiConnection, ApiGrant

log = logging.getLogger("opti.connections")

MAX_RESPONSE_BYTES = 400_000          # au-delà, la réponse est refusée (demander des filtres plus précis)
MAX_CALLS_PER_MINUTE = 30             # par utilisateur : protège le système distant d'une boucle
MAX_VALUE_LEN = 200


# ── Description d'une opération ──────────────────────────────────────────────────────────────────────────────
class Param(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.\-]{0,48}$")
    where: Literal["path", "query"] = "query"
    type: Literal["string", "integer", "number", "boolean"] = "string"
    required: bool = False
    description: str = Field(default="", max_length=300)
    enum: list[str] | None = Field(default=None, max_length=30)


class Operation(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,40}$")
    label: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=800)       # lue par le modèle : dit quand et comment s'en servir
    method: Literal["GET"] = "GET"                              # lecture seule : aucune autre méthode n'est possible
    path: str = Field(max_length=300)
    params: list[Param] = Field(default_factory=list, max_length=12)
    enabled: bool = False
    max_chars: int = Field(default=8000, ge=500, le=30000)      # taille maximale du résultat donné au modèle

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        if (not v.startswith("/") or "//" in v or ".." in v or "?" in v or "#" in v or "://" in v
                or not re.fullmatch(r"[A-Za-z0-9_\-./{}~:%@]+", v)):
            raise ValueError("chemin invalide (attendu : /chemin/{parametre}, sans adresse ni « ? »)")
        return v

    @model_validator(mode="after")
    def _coherent(self):
        names = [p.name for p in self.params]
        if len(set(names)) != len(names):
            raise ValueError("deux paramètres portent le même nom")
        in_path = set(re.findall(r"\{([^{}]+)\}", self.path))
        declared = {p.name for p in self.params if p.where == "path"}
        if in_path != declared:
            raise ValueError("les {paramètres} du chemin doivent correspondre aux paramètres de type « chemin »")
        for p in self.params:
            if p.where == "path":
                p.required = True
        return self


def slugify(name: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return (re.sub(r"[^a-z0-9]+", "_", t).strip("_") or "api")[:20]


def operations_of(conn: ApiConnection) -> list[Operation]:
    out = []
    try:
        raw = json.loads(conn.operations or "[]")
    except ValueError:
        return out
    for o in raw:
        try:
            out.append(Operation(**o))
        except Exception:
            log.warning("Opération invalide ignorée dans la connexion %s", conn.name)
    return out


def check_base_url(url: str) -> str:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("L'adresse doit commencer par http:// ou https://")
    if u.username or u.password or u.query or u.fragment:
        raise ValueError("L'adresse ne doit contenir ni identifiants, ni paramètres, ni ancre (ils se règlent à part)")
    return url.strip().rstrip("/")


# ── Accès ────────────────────────────────────────────────────────────────────────────────────────────────────
def granted_ids(db: Session, user_id: str) -> set[str]:
    return set(db.scalars(select(ApiGrant.connection_id).where(ApiGrant.user_id == user_id)).all())


def granted_ids_of_connection(db: Session, conn_id: str) -> set[str]:
    return set(db.scalars(select(ApiGrant.user_id).where(ApiGrant.connection_id == conn_id)).all())


def accessible(db: Session, user_id: str, disabled: list[str] | None = None) -> list[ApiConnection]:
    """Connexions actives que cet utilisateur peut utiliser (et qu'il n'a pas désactivées pour lui)."""
    conns = db.scalars(select(ApiConnection).where(ApiConnection.enabled.is_(True)).order_by(ApiConnection.name)).all()
    granted = granted_ids(db, user_id)
    off = set(disabled or [])
    return [c for c in conns if (c.allow_all or c.id in granted) and c.id not in off]


def build_tools(db: Session, user_id: str, disabled: list[str] | None = None) -> tuple[list[dict], dict[str, tuple[str, str]]]:
    """Outils proposés au modèle : ([définitions au format function-calling], {nom d'outil: (id connexion, id opération)})."""
    tools, index = [], {}
    for c in accessible(db, user_id, disabled):
        for op in operations_of(c):
            if not op.enabled:
                continue
            props, required = {}, []
            for p in op.params:
                schema: dict[str, Any] = {"type": p.type, "description": p.description or p.name}
                if p.enum:
                    schema["enum"] = p.enum
                props[p.name] = schema
                if p.required:
                    required.append(p.name)
            name = f"{c.slug}__{op.id}"
            tools.append({"type": "function", "function": {
                "name": name, "description": f"[{c.name}] {op.description or op.label or op.id}",
                "parameters": {"type": "object", "properties": props, "required": required}}})
            index[name] = (c.id, op.id)
    return tools, index


# ── Exécution d'un appel ─────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Outcome:
    text: str                                   # ce que reçoit le modèle
    card: dict = field(default_factory=dict)    # ce que voit l'utilisateur (jamais le résultat lui-même)


_calls: dict[str, deque] = defaultdict(deque)


def _rate_limited(user_id: str) -> bool:
    now, q = time.monotonic(), _calls[user_id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= MAX_CALLS_PER_MINUTE:
        return True
    q.append(now)
    return False


def _cast(p: Param, value: Any) -> Any:
    """Valide et convertit une valeur donnée par le modèle. Lève ValueError avec un message lisible."""
    if isinstance(value, (dict, list)):
        raise ValueError(f"« {p.name} » doit être une valeur simple")
    if p.type == "integer":
        try:
            return int(str(value).strip())
        except ValueError:
            raise ValueError(f"« {p.name} » doit être un nombre entier")
    if p.type == "number":
        try:
            return float(str(value).strip().replace(",", "."))
        except ValueError:
            raise ValueError(f"« {p.name} » doit être un nombre")
    if p.type == "boolean":
        v = str(value).strip().lower()
        if v in ("true", "1", "oui", "vrai"):
            return "true"
        if v in ("false", "0", "non", "faux"):
            return "false"
        raise ValueError(f"« {p.name} » doit être vrai ou faux")
    s = str(value).strip()
    if len(s) > MAX_VALUE_LEN or re.search(r"[\x00-\x1f\x7f]", s):
        raise ValueError(f"« {p.name} » est trop long ou contient des caractères interdits")
    if p.where == "path" and s in (".", ".."):
        raise ValueError(f"« {p.name} » est invalide")
    if p.enum:
        match = next((e for e in p.enum if e.lower() == s.lower()), None)
        if match is None:
            raise ValueError(f"« {p.name} » doit valoir l'une de ces valeurs : {', '.join(p.enum)}")
        return match
    return s


def _auth(conn: ApiConnection) -> tuple[dict, httpx.Auth | None]:
    secret = vault.decrypt(conn.secret_enc)
    headers = {"Accept": "application/json", "User-Agent": "AssistantOpti/1.0"}
    if conn.auth_type == "bearer" and secret:
        headers["Authorization"] = f"Bearer {secret}"
    elif conn.auth_type == "header" and secret and conn.auth_header:
        headers[conn.auth_header] = secret
    elif conn.auth_type == "basic":
        return headers, httpx.BasicAuth(conn.username, secret)
    return headers, None


def _verify(conn: ApiConnection):
    if conn.verify_tls is False:
        return False
    return config.CA_BUNDLE or True


def _timeout(conn: ApiConnection) -> httpx.Timeout:
    return httpx.Timeout(float(conn.timeout_s or 15), connect=8.0)


def _compact(data: Any, limit: int) -> tuple[str, int | None]:
    """JSON compact tenant dans `limit` caractères : une longue liste est raccourcie (avec sa taille réelle indiquée)."""
    def dump(x):
        return json.dumps(x, ensure_ascii=False, separators=(",", ":"))
    text = dump(data)
    if len(text) <= limit:
        return text, (len(data) if isinstance(data, list) else None)

    def shrink(lst: list) -> list:
        lo, keep = 0, len(lst)
        while lo < keep:                                     # plus grand nombre d'éléments qui tient
            mid = (lo + keep + 1) // 2
            if len(dump(lst[:mid])) + 160 <= limit:
                lo = mid
            else:
                keep = mid - 1
        return lst[:lo]

    if isinstance(data, list):
        kept = shrink(data)
        return dump({"_note": f"{len(data)} éléments au total ; seuls les {len(kept)} premiers sont fournis (affinez la demande avec un filtre)",
                     "éléments": kept}), len(data)
    if isinstance(data, dict):
        big = max((k for k, v in data.items() if isinstance(v, list)), key=lambda k: len(dump(data[k])), default=None)
        if big is not None:
            total, kept = len(data[big]), shrink(data[big])
            trimmed = {**data, big: kept, "_note": f"« {big} » : {total} éléments au total ; seuls les {len(kept)} premiers sont fournis"}
            if len(dump(trimmed)) <= limit + 400:
                return dump(trimmed), total
    return text[:limit] + " … [résultat tronqué]", None


def _status_message(code: int) -> str:
    return {
        401: "Le système a refusé l'authentification de la connexion (401). Signalez-le à l'administrateur.",
        403: "Le système refuse l'accès à cette donnée (403).",
        404: "Aucun élément trouvé (404).",
        429: "Le système reçoit trop de demandes (429) : réessayez plus tard.",
    }.get(code, f"Le système a répondu avec une erreur ({code}).")


async def call_operation(conn: ApiConnection, op: Operation, args: dict) -> tuple[bool, str, dict]:
    """Exécute l'opération. Renvoie (succès, texte pour le modèle, infos pour la carte : status, ms, count, error, params)."""
    info: dict[str, Any] = {"status": None, "ms": 0, "count": None, "error": None, "params": {}}
    values: dict[str, Any] = {}
    for p in op.params:
        raw = args.get(p.name)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if p.required:
                info["error"] = f"Paramètre obligatoire manquant : {p.name}"
                return False, info["error"], info
            continue
        try:
            values[p.name] = _cast(p, raw)
        except ValueError as e:
            info["error"] = str(e)
            return False, f"Erreur : {e}", info
    info["params"] = {k: str(v)[:60] for k, v in values.items()}

    path = op.path
    for p in op.params:
        if p.where == "path":
            path = path.replace("{" + p.name + "}", quote(str(values[p.name]), safe=""))
    query = {p.name: values[p.name] for p in op.params if p.where == "query" and p.name in values}
    url = conn.base_url.rstrip("/") + path
    headers, auth = _auth(conn)

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_timeout(conn), verify=_verify(conn), follow_redirects=False, auth=auth) as client:
            async with client.stream("GET", url, params=query, headers=headers) as resp:
                info["status"] = resp.status_code
                body, size = b"", 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        info["error"] = "Réponse trop volumineuse"
                        info["ms"] = int((time.monotonic() - started) * 1000)
                        return False, "Erreur : la réponse du système est trop volumineuse. Précisez la demande avec un filtre.", info
                    body += chunk
                ctype = resp.headers.get("content-type", "")
    except httpx.TimeoutException:
        info["error"] = "Délai dépassé"
        info["ms"] = int((time.monotonic() - started) * 1000)
        return False, "Erreur : le système n'a pas répondu à temps. Réessayez plus tard.", info
    except httpx.HTTPError as e:
        log.warning("Appel %s impossible : %s", conn.name, type(e).__name__)
        info["error"] = "Système injoignable"
        info["ms"] = int((time.monotonic() - started) * 1000)
        return False, "Erreur : le système est injoignable pour le moment.", info
    info["ms"] = int((time.monotonic() - started) * 1000)

    if info["status"] // 100 != 2:
        info["error"] = _status_message(info["status"])
        return False, f"Erreur : {info['error']}", info
    text = body.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError:
        if "json" in ctype.lower() or not text.strip():
            info["error"] = "Réponse illisible"
            return False, "Erreur : la réponse du système n'est pas exploitable.", info
        data = text[:op.max_chars]
    compact, count = _compact(data, op.max_chars)
    info["count"] = count
    try:
        parsed: Any = json.loads(compact)
    except ValueError:                                            # texte brut, ou JSON tronqué faute de place
        parsed = compact
    return True, json.dumps({"source": conn.name, "opération": op.label or op.id, "résultat": parsed}, ensure_ascii=False), info


async def execute(db: Session, user, tool_name: str, args: Any, index: dict[str, tuple[str, str]], *,
                  request=None, force: bool = False) -> Outcome:
    """
    Exécute l'appel d'outil demandé par le modèle pour `user` (objet avec id et username). `force` (essai de l'administrateur) ignore
    l'activation de l'opération et les autorisations. Tout est journalisé (jamais les secrets ni le résultat).
    """
    if not isinstance(args, dict):
        args = {}
    if force:
        slug, _, op_id = tool_name.partition("__")
        conn = db.scalar(select(ApiConnection).where(ApiConnection.slug == slug))
        op = next((o for o in operations_of(conn) if o.id == op_id), None) if conn else None
    else:
        target = index.get(tool_name)
        conn = db.get(ApiConnection, target[0]) if target else None
        op = next((o for o in operations_of(conn) if o.id == target[1] and o.enabled), None) if conn else None
        if conn and not (conn.enabled and (conn.allow_all or conn.id in granted_ids(db, user.id))):
            conn = op = None                                    # droits retirés depuis le début de la conversation
    if not conn or not op:
        return Outcome("Erreur : cet outil n'est pas disponible.", {"connection": "?", "operation": tool_name, "ok": False, "error": "Outil indisponible"})

    card = {"connection": conn.name, "operation": op.label or op.id}
    if not force and _rate_limited(user.id):
        card.update(ok=False, error="Trop d'appels")
        return Outcome("Erreur : trop d'appels en peu de temps. Réessayez dans une minute.", card)

    ok, text, info = await call_operation(conn, op, args)
    card.update(ok=ok, status=info["status"], ms=info["ms"], count=info["count"], error=info["error"], params=info["params"])
    audit.record(db, "api.try" if force else "api.call", actor=user, target=f"{conn.name} · {op.id}", request=request,
                 detail={"params": info["params"], "status": info["status"], "ms": info["ms"], "ok": ok, **({"erreur": info["error"]} if info["error"] else {})})
    return Outcome(text, card)


# ── Test de connexion ────────────────────────────────────────────────────────────────────────────────────────
async def test_connection(conn: ApiConnection) -> dict:
    path = conn.test_path or "/"
    url = conn.base_url.rstrip("/") + (path if path.startswith("/") else "/" + path)
    headers, auth = _auth(conn)
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_timeout(conn), verify=_verify(conn), follow_redirects=False, auth=auth) as client:
            r = await client.get(url, headers=headers)
    except httpx.TimeoutException:
        return {"ok": False, "reachable": False, "message": "Délai dépassé : le système ne répond pas. Vérifiez l'adresse, le pare-feu et le tunnel réseau."}
    except httpx.ConnectError as e:
        if "CERTIFICATE" in str(e).upper():
            return {"ok": False, "reachable": False, "message": "Certificat TLS non reconnu. Ajoutez l'autorité de certification interne (variable OPTI_CA_BUNDLE) ou décochez « Vérifier le certificat »."}
        return {"ok": False, "reachable": False, "message": "Connexion impossible : adresse introuvable ou port fermé. Vérifiez l'adresse et le réseau."}
    except httpx.HTTPError as e:
        return {"ok": False, "reachable": False, "message": f"Échec de la requête ({type(e).__name__})."}
    ms, code = int((time.monotonic() - started) * 1000), r.status_code
    if code // 100 == 2:
        return {"ok": True, "reachable": True, "status": code, "ms": ms, "message": f"Connexion réussie ({code}, {ms} ms)."}
    if code in (401, 403):
        return {"ok": False, "reachable": True, "status": code, "ms": ms, "message": f"Le système répond mais refuse l'authentification ({code}). Vérifiez le jeton, la clé ou l'identifiant."}
    if code == 404:
        return {"ok": False, "reachable": True, "status": code, "ms": ms, "message": "Le système répond, mais l'adresse de test est introuvable (404). Indiquez un « chemin de test » valide (par exemple /api/ping)."}
    if code // 100 == 3:
        return {"ok": False, "reachable": True, "status": code, "ms": ms, "message": f"Le système redirige ({code}) : utilisez l'adresse finale, les redirections ne sont pas suivies."}
    return {"ok": False, "reachable": True, "status": code, "ms": ms, "message": f"Le système répond avec une erreur ({code})."}


# ── Import d'une spécification OpenAPI / Swagger ─────────────────────────────────────────────────────────────
_TYPES = {"integer": "integer", "number": "number", "boolean": "boolean"}


def _ref(spec: dict, node: Any) -> Any:
    seen = 0
    while isinstance(node, dict) and "$ref" in node and seen < 5:
        cur: Any = spec
        for part in str(node["$ref"]).lstrip("#/").split("/"):
            cur = cur.get(part, {}) if isinstance(cur, dict) else {}
        node, seen = cur, seen + 1
    return node


def parse_spec(spec: dict, spec_url: str | None = None) -> dict:
    """Extrait les opérations de CONSULTATION (GET) d'une spécification. Aucune n'est activée : c'est à l'administrateur de choisir."""
    if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict):
        raise ValueError("Ce document n'est pas une spécification OpenAPI / Swagger (pas de section « paths »)")
    base = ""
    if spec.get("servers"):
        base = str(spec["servers"][0].get("url", ""))
    elif spec.get("host"):
        base = f"{(spec.get('schemes') or ['https'])[0]}://{spec['host']}{spec.get('basePath', '')}"
    if base.startswith("/") and spec_url:
        u = urlparse(spec_url)
        base = f"{u.scheme}://{u.netloc}{base}"
    ops, used, skipped = [], set(), 0
    for path, item in list(spec["paths"].items())[:500]:
        item = _ref(spec, item)
        if not isinstance(item, dict) or not isinstance(item.get("get"), dict):
            continue
        get = item["get"]
        params = []
        for raw in list(item.get("parameters", [])) + list(get.get("parameters", [])):
            p = _ref(spec, raw)
            if not isinstance(p, dict) or p.get("in") not in ("path", "query") or not p.get("name"):
                continue
            schema = _ref(spec, p.get("schema", {})) or {}
            typ = _TYPES.get(schema.get("type") or p.get("type"), "string")
            enum = schema.get("enum") or p.get("enum")
            params.append({"name": str(p["name"]), "where": p["in"], "type": typ, "required": bool(p.get("required")) or p["in"] == "path",
                           "description": str(p.get("description") or "")[:300],
                           **({"enum": [str(e) for e in enum][:30]} if isinstance(enum, list) and enum else {})})
        oid = re.sub(r"[^a-z0-9]+", "_", str(get.get("operationId") or "").lower()).strip("_")
        if not oid:
            oid = re.sub(r"[^a-z0-9]+", "_", "_".join(s for s in path.split("/") if s and not s.startswith("{")).lower()).strip("_") or "operation"
        if not oid[0].isalpha():
            oid = "op_" + oid
        oid, n = oid[:38], 2
        while oid in used:
            oid = f"{oid[:34]}_{n}"
            n += 1
        used.add(oid)
        try:
            op = Operation(id=oid, label=str(get.get("summary") or oid)[:120],
                           description=str(get.get("description") or get.get("summary") or "")[:800], path=path, params=params, enabled=False)
        except Exception:
            skipped += 1
            continue
        ops.append(op.model_dump())
    return {"title": str((spec.get("info") or {}).get("title") or ""), "base_url": base.rstrip("/"), "operations": ops, "skipped": skipped}


async def load_spec(url: str | None, text: str | None, conn: ApiConnection | None = None) -> dict:
    """Lit une spécification depuis une adresse (avec l'authentification de la connexion si elle est fournie) ou depuis un texte collé."""
    if url:
        headers, auth = _auth(conn) if conn else ({"Accept": "application/json, application/yaml, */*"}, None)
        try:
            async with httpx.AsyncClient(timeout=20.0, verify=_verify(conn) if conn else True, follow_redirects=True, auth=auth) as client:
                r = await client.get(url, headers=headers)
        except httpx.HTTPError as e:
            raise ValueError(f"Impossible de lire la spécification ({type(e).__name__}). Vérifiez l'adresse.")
        if r.status_code != 200:
            raise ValueError(f"La spécification n'est pas accessible (code {r.status_code}).")
        if len(r.content) > 3_000_000:
            raise ValueError("Spécification trop volumineuse (3 Mo au maximum).")
        text = r.text
    if not text or not text.strip():
        raise ValueError("Aucune spécification fournie.")
    if len(text) > 3_000_000:
        raise ValueError("Spécification trop volumineuse (3 Mo au maximum).")
    try:
        spec = json.loads(text)
    except ValueError:
        try:
            spec = yaml.safe_load(text)
        except yaml.YAMLError:
            raise ValueError("La spécification n'est ni du JSON ni du YAML valide.")
    return parse_spec(spec, url)
