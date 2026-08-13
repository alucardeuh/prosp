"""
Jobs en arrière-plan pour le dashboard.

Avant, cliquer "Qualifier" ou "Générer" gelait la page pendant toute la
durée des appels API (30 s à plusieurs minutes) sans aucun retour visuel.
Maintenant chaque action longue tourne dans un thread ; l'interface
interroge /api/jobs/<id> toutes les secondes et affiche la progression
en direct.

App locale mono-utilisateur : un registre en mémoire suffit (un job
interrompu par un redémarrage se relance d'un clic, et les brouillons
déjà générés sont persistés en base au fil de l'eau, donc rien n'est perdu).
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime

_JOBS: dict[str, dict] = {}
_VERROU = threading.Lock()
# Un seul job à la fois : évite de lancer deux générations en parallèle
# par double-clic et de payer deux fois les mêmes appels API.
_JOB_EN_COURS = threading.Lock()


def get_job(job_id: str) -> dict | None:
    with _VERROU:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def job_actif() -> dict | None:
    """Le job en cours d'exécution, s'il y en a un."""
    with _VERROU:
        for job in _JOBS.values():
            if job["etat"] == "en_cours":
                return dict(job)
    return None


def _maj(job_id: str, **champs) -> None:
    with _VERROU:
        _JOBS[job_id].update(champs)


def _log(job_id: str, ligne: str) -> None:
    with _VERROU:
        _JOBS[job_id]["log"].append(ligne)


def lancer(titre: str, elements: list, traiter_un, terminer=None) -> str:
    """Lance un job qui applique `traiter_un(element, log)` à chaque élément.

    - traiter_un(element, log) -> str | None : traite un élément, retourne
      une ligne de log (ou lève une exception, qui est loggée sans arrêter
      le job).
    - terminer(log) -> str | None : optionnel, appelé à la fin.

    Retourne l'id du job. Lève RuntimeError si un job tourne déjà.
    """
    if not _JOB_EN_COURS.acquire(blocking=False):
        raise RuntimeError("Une action est déjà en cours — attends qu'elle se termine.")

    job_id = uuid.uuid4().hex[:12]
    with _VERROU:
        _JOBS[job_id] = {
            "id": job_id,
            "titre": titre,
            "etat": "en_cours",       # en_cours | termine | echec
            "fait": 0,
            "total": len(elements),
            "log": [],
            "erreurs": 0,
            "demarre_a": datetime.now().strftime("%H:%M:%S"),
        }

    def _executer():
        try:
            for element in elements:
                try:
                    ligne = traiter_un(element, lambda l: _log(job_id, l))
                    if ligne:
                        _log(job_id, ligne)
                except Exception as exc:  # noqa: BLE001 - un échec ne stoppe pas le lot
                    _log(job_id, f"❌ {exc}")
                    with _VERROU:
                        _JOBS[job_id]["erreurs"] += 1
                with _VERROU:
                    _JOBS[job_id]["fait"] += 1
            if terminer:
                ligne = terminer(lambda l: _log(job_id, l))
                if ligne:
                    _log(job_id, ligne)
            _maj(job_id, etat="termine")
        except Exception as exc:  # noqa: BLE001 - échec global (ex : Gmail inaccessible)
            _log(job_id, f"❌ Échec du job : {exc}")
            _maj(job_id, etat="echec")
        finally:
            _JOB_EN_COURS.release()

    threading.Thread(target=_executer, daemon=True).start()
    return job_id
