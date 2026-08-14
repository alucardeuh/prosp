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

try:
    import anthropic as _anthropic_sdk
except ImportError:  # ne devrait jamais arriver (dépendance du projet), mais ne doit jamais faire planter jobs.py
    _anthropic_sdk = None


def _message_lisible(exc: Exception) -> str:
    """Traduit les erreurs API Anthropic les plus courantes en message
    clair et actionnable, plutôt que le dump technique brut de l'exception
    (ex : "Error code: 401 - {'type': 'error', 'error': {...}}") — surtout
    utile pour une clé invalide/absente, l'erreur la plus probable pour
    quelqu'un qui découvre l'outil."""
    if _anthropic_sdk:
        if isinstance(exc, _anthropic_sdk.AuthenticationError):
            return "Clé API Anthropic invalide ou expirée — vérifie-la dans Paramètres."
        if isinstance(exc, _anthropic_sdk.RateLimitError):
            return "Limite de débit API Anthropic atteinte — réessaie dans quelques instants."
        if isinstance(exc, _anthropic_sdk.APIConnectionError):
            return "Impossible de joindre l'API Anthropic — vérifie ta connexion internet."
    return str(exc)


_JOBS: dict[str, dict] = {}
_VERROU = threading.Lock()
# Un seul job à la fois : évite de lancer deux générations en parallèle
# par double-clic et de payer deux fois les mêmes appels API.
_JOB_EN_COURS = threading.Lock()

# Nombre de jobs terminés conservés en mémoire. Sans plafond, _JOBS grossit
# pour toujours tant que le process tourne (un job de plus à chaque clic sur
# "Qualifier", "Générer les brouillons", etc.) — une vraie fuite mémoire à
# l'usage quotidien, même si chacune est petite. Le job en cours n'est
# jamais purgé, seulement les plus anciens déjà terminés.
_JOBS_CONSERVES = 20


def _purger_anciens_jobs() -> None:
    with _VERROU:
        if len(_JOBS) <= _JOBS_CONSERVES:
            return
        # _JOBS est un dict Python : l'ordre d'insertion est préservé, donc
        # les premières clés sont les plus anciennes.
        for job_id in list(_JOBS.keys()):
            if len(_JOBS) <= _JOBS_CONSERVES:
                break
            if _JOBS[job_id]["etat"] != "en_cours":
                del _JOBS[job_id]


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


def demander_annulation(job_id: str) -> bool:
    """Demande l'arrêt d'un job en cours. Ne peut pas interrompre l'appel
    API déjà en vol pour l'élément courant (impossible à couper proprement
    depuis l'extérieur), mais empêche tous les suivants de démarrer — donc
    stoppe le gaspillage de tokens en quelques secondes plutôt qu'en laissant
    tourner un job jusqu'au bout. Retourne False si le job est introuvable
    ou déjà terminé."""
    with _VERROU:
        job = _JOBS.get(job_id)
        if not job or job["etat"] != "en_cours":
            return False
        job["annulation_demandee"] = True
    return True


def lancer(titre: str, elements: list, traiter_un, terminer=None) -> str:
    """Lance un job qui applique `traiter_un(element, log)` à chaque élément.

    - traiter_un(element, log) -> str | None : traite un élément, retourne
      une ligne de log (ou lève une exception, qui est loggée sans arrêter
      le job).
    - terminer(log) -> str | None : optionnel, appelé à la fin (seulement
      si le job n'a pas été annulé en cours de route).

    Retourne l'id du job. Lève RuntimeError si un job tourne déjà.
    """
    if not _JOB_EN_COURS.acquire(blocking=False):
        raise RuntimeError("Une action est déjà en cours — attends qu'elle se termine.")

    _purger_anciens_jobs()
    job_id = uuid.uuid4().hex[:12]
    with _VERROU:
        _JOBS[job_id] = {
            "id": job_id,
            "titre": titre,
            "etat": "en_cours",       # en_cours | termine | echec | annule
            "fait": 0,
            "total": len(elements),
            "log": [],
            "erreurs": 0,
            "annulation_demandee": False,
            "demarre_a": datetime.now().strftime("%H:%M:%S"),
        }

    def _executer():
        annule = False
        try:
            for element in elements:
                with _VERROU:
                    annule = _JOBS[job_id]["annulation_demandee"]
                if annule:
                    break
                try:
                    ligne = traiter_un(element, lambda l: _log(job_id, l))
                    if ligne:
                        _log(job_id, ligne)
                except Exception as exc:  # noqa: BLE001 - un échec ne stoppe pas le lot
                    _log(job_id, f"❌ {_message_lisible(exc)}")
                    with _VERROU:
                        _JOBS[job_id]["erreurs"] += 1
                with _VERROU:
                    _JOBS[job_id]["fait"] += 1
            if annule:
                _log(job_id, "⏹ Annulé — ce qui était déjà traité reste enregistré.")
                _maj(job_id, etat="annule")
            else:
                if terminer:
                    ligne = terminer(lambda l: _log(job_id, l))
                    if ligne:
                        _log(job_id, ligne)
                _maj(job_id, etat="termine")
        except Exception as exc:  # noqa: BLE001 - échec global (ex : Gmail inaccessible)
            _log(job_id, f"❌ Échec du job : {_message_lisible(exc)}")
            _maj(job_id, etat="echec")
        finally:
            _JOB_EN_COURS.release()

    threading.Thread(target=_executer, daemon=True).start()
    return job_id
