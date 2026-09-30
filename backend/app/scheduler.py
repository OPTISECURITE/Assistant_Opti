"""
Répartition des places Ollama entre l'assistant et l'agent vocal.

Le GPU est partagé : Ollama sert au plus `ollama_slots` requêtes à la fois, et chaque appel téléphonique en occupe une
quand l'agent réfléchit. L'assistant ne prend donc jamais que les places que les appels en cours ne réclament pas :

    places de l'assistant = ollama_slots - appels en cours - réserve   (au moins 1, au plus max_assistant_slots)

Une conversation ordinaire prend 1 place ; la lecture complète d'un long document en prend 2 (deux sections en parallèle).
Les demandes qui ne peuvent pas démarrer attendent en file (premier arrivé, premier servi). Un même utilisateur ne peut
pas occuper plus de `max_per_user` places à lui seul.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field

from . import settings, voice

log = logging.getLogger("opti.scheduler")


@dataclass(eq=False)
class Job:
    user_id: str
    weight: int = 1
    heavy: bool = False
    created: float = field(default_factory=time.monotonic)
    event: asyncio.Event = field(default_factory=asyncio.Event)
    started: bool = False


class Scheduler:
    def __init__(self) -> None:
        self.running: list[Job] = []
        self.queue: list[Job] = []

    def capacity(self) -> int:
        s = settings.app()
        return max(1, min(s.max_assistant_slots, s.ollama_slots - voice.calls() - s.voice_reserve))

    def used(self) -> int:
        return sum(j.weight for j in self.running)

    def position(self, job: Job) -> int:
        try:
            return self.queue.index(job) + 1
        except ValueError:
            return 0

    def enqueue(self, user_id: str, weight: int = 1, heavy: bool = False) -> Job:
        job = Job(user_id=user_id, weight=weight, heavy=heavy)
        self.queue.append(job)
        self.dispatch()
        return job

    def dispatch(self) -> None:
        """Démarre les demandes en attente qui peuvent l'être (appelé à chaque changement, et toutes les 2 s)."""
        capacity = self.capacity()
        per_user = settings.app().max_per_user
        for job in list(self.queue):
            if sum(1 for j in self.running if j.user_id == job.user_id) >= per_user:
                continue                                    # cet utilisateur a déjà ses places : on passe au suivant
            if self.running and self.used() + job.weight > capacity:
                break                                       # pas de place : on respecte l'ordre d'arrivée
            self.queue.remove(job)
            self.running.append(job)
            job.started = True
            job.event.set()

    def finish(self, job: Job) -> None:
        """Libère la place (ou abandonne l'attente) ; sans danger si appelé plusieurs fois."""
        if job in self.queue:
            self.queue.remove(job)
        if job in self.running:
            self.running.remove(job)
        self.dispatch()

    def snapshot(self) -> dict:
        s = settings.app()
        return {"voice_calls": voice.calls(), "capacity": self.capacity(), "used": self.used(),
                "running": len(self.running), "queued": len(self.queue), "ollama_slots": s.ollama_slots,
                "max_assistant_slots": s.max_assistant_slots}


scheduler = Scheduler()


async def poll_loop() -> None:
    """Réévalue la file régulièrement : un appel qui se termine libère des places sans qu'aucune requête ne le signale."""
    while True:
        await asyncio.sleep(2)
        try:
            scheduler.dispatch()
        except Exception:
            log.exception("Réévaluation de la file impossible")
