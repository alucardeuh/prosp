"""
Planificateur d'envois programmés.

Un brouillon peut porter une date/heure cible (date_envoi_prevue) plutôt que
d'être envoyé tout de suite sur clic. Ce thread de fond vérifie
régulièrement s'il y a un envoi arrivé à échéance et l'envoie
automatiquement, en passant par la MÊME fonction que l'envoi manuel — donc
avec exactement les mêmes garde-fous (désinscription, email invalide, quota
du jour).

Limite honnête à connaître : ça ne fonctionne que tant que l'app tourne,
comme les autres jobs. Si le Terminal est fermé avant l'heure prévue,
l'envoi partira au prochain démarrage de l'app (rattrapage) plutôt qu'à
l'heure exacte annoncée — pas un vrai service en arrière-plan du système.
"""
from __future__ import annotations

import threading
import time

from db import database as db

INTERVALLE_VERIFICATION = 30  # secondes


def _envoyer_dus() -> None:
    from agents import email_sender  # import tardif : évite un cycle au chargement du module

    for brouillon in db.list_brouillons_programmes_dus():
        prospect_id = brouillon["prospect_id"]
        try:
            email_sender.envoyer_brouillon(prospect_id)
            print(f"📅 Envoi programmé effectué pour le prospect {prospect_id}.")
        except ValueError as exc:
            # Garde-fou légitime (quota atteint, désinscrit, email invalide...) :
            # on laisse la programmation en place, elle sera retentée au
            # prochain passage (utile si c'est juste le quota qui manque,
            # par exemple — ça se libère le lendemain).
            print(f"⏳ Envoi programmé du prospect {prospect_id} reporté : {exc}")
        except Exception as exc:  # noqa: BLE001 - ne doit jamais arrêter la boucle
            print(f"⚠️  Envoi programmé du prospect {prospect_id} a échoué : {exc}")


def _boucle() -> None:
    while True:
        try:
            _envoyer_dus()
        except Exception as exc:  # noqa: BLE001 - la boucle ne doit jamais mourir
            print(f"⚠️  Erreur dans le planificateur : {exc}")
        time.sleep(INTERVALLE_VERIFICATION)


def demarrer() -> None:
    """À appeler une fois au démarrage de l'app."""
    threading.Thread(target=_boucle, daemon=True).start()
