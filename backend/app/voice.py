"""
Détection des appels en cours sur l'agent vocal, sans rien modifier dans celui-ci.

Chaque appel garde une connexion ouverte vers le STT et le TTS Kyutai (ports 8080 et 8089 par défaut). Le nombre de
connexions établies sur ces ports donne donc le nombre d'appels en cours. Le serveur et l'assistant tournent sur la
même machine : on lit simplement la table des connexions du noyau (/proc/net/tcp).
"""
import time

from . import config

_cache: tuple[float, int] = (0.0, 0)
_own = 0      # connexions ouvertes par l'assistant lui-même sur ces ports (dictée vocale) : à ne pas compter comme des appels


def set_own(n: int) -> None:
    global _own
    _own = max(0, n)


def _ports() -> set[int]:
    return {int(p) for p in config.VOICE_PORTS.split(",") if p.strip().isdigit()}


def calls() -> int:
    """Nombre d'appels en cours (le plus grand nombre de connexions établies sur l'un des ports du STT / TTS)."""
    global _cache
    now = time.monotonic()
    if now - _cache[0] < 2:
        return _cache[1]
    per_port = {p: 0 for p in _ports()}
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path) as f:
                lines = f.read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 4 or fields[3] != "01":       # 01 = ESTABLISHED
                continue
            try:
                port = int(fields[1].rsplit(":", 1)[1], 16)   # port local
            except (IndexError, ValueError):
                continue
            if port in per_port:
                per_port[port] += 1
    busiest = max(per_port.values(), default=0)
    n = max(0, -(-max(0, busiest - _own) // config.VOICE_CONNS_PER_CALL))   # arrondi au supérieur
    _cache = (now, n)
    return n
