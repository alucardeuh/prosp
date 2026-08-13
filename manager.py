"""
Manager — orchestre les agents automatiques selon l'état des prospects.

V1 volontairement simple : une state machine + un enchaînement séquentiel,
pas un agent Claude qui déciderait dynamiquement quoi faire (ça, c'est la
V2 si un jour c'est nécessaire). Prévisible et facile à débugger, comme le
reste du projet jusqu'ici.

Ce que le manager déclenche automatiquement (aucune décision irréversible) :
    - agent qualification : nouveau -> qualifie / disqualifie
    - agent email (lecture) : classe les réponses reçues

Ce qu'il NE déclenche JAMAIS automatiquement, volontairement :
    - agent email (envoi) : nécessite une validation humaine à chaque email,
      donc il ne peut pas tourner sans surveillance. Le manager te dit
      combien de prospects attendent et te renvoie vers la commande à lancer
      toi-même.
    - agent LinkedIn / réseaux sociaux : pas encore construits.

Usage :
    python3 -m manager                 # cycle complet (qualification + lecture email)
    python3 -m manager --dry-run       # simule tout, sans API Claude ni Gmail
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from db import database as db  # noqa: E402
from agents import qualification, email_reader  # noqa: E402

STATUTS = ["nouveau", "qualifie", "disqualifie", "contacte", "repondu", "rdv", "perdu", "desinscrit"]


def resume_pipeline() -> dict[str, int]:
    """Nombre de prospects par statut — c'est l'état complet du pipeline."""
    return {s: len(db.list_prospects(statut=s)) for s in STATUTS}


def afficher_resume(titre: str, resume: dict[str, int]) -> None:
    print(f"\n{titre}")
    non_vides = {s: c for s, c in resume.items() if c}
    if not non_vides:
        print("  (base vide)")
        return
    for statut, count in non_vides.items():
        print(f"  {statut:15s} : {count}")


def run(dry_run: bool = False) -> None:
    if not dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY n'est pas définie (vérifie ton .env). "
            "Utilise --dry-run pour tester sans clé.",
            file=sys.stderr,
        )
        sys.exit(1)

    db.init_db()

    print("=" * 60)
    print("CYCLE MANAGER")
    print("=" * 60)

    afficher_resume("État avant cycle", resume_pipeline())

    print("\n[1/2] Agent qualification...")
    try:
        qualification.run(dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 - un agent qui échoue ne doit pas bloquer l'autre
        print(f"  ❌ Agent qualification a échoué : {exc}", file=sys.stderr)

    print("\n[2/2] Agent email (lecture)...")
    try:
        email_reader.run(dry_run=dry_run, test_connexion=False)
    except FileNotFoundError as exc:
        print(f"  ❌ Agent email (lecture) : {exc}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ Agent email (lecture) a échoué : {exc}", file=sys.stderr)

    apres = resume_pipeline()
    afficher_resume("État après cycle", apres)

    print()
    en_attente_envoi = apres.get("qualifie", 0)
    if en_attente_envoi:
        print(f"👉 {en_attente_envoi} prospect(s) qualifié(s) en attente d'envoi.")
        print("   Lance 'python3 -m agents.email_sender' pour les revoir un par un.")

    en_attente_action = apres.get("repondu", 0)
    if en_attente_action:
        print(f"👉 {en_attente_action} prospect(s) ont répondu et attendent une décision humaine (statut 'repondu').")

    try:
        delai = int(db.get_reglage("delai_relance_jours") or 7)
        maxr = int(db.get_reglage("max_relances") or 2)
        dues = len(db.prospects_a_relancer(db.profil_actif(), delai, maxr))
        if dues:
            print(f"👉 {dues} relance(s) due(s) — page /relances de l'interface.")
    except Exception:  # noqa: BLE001 - le résumé ne doit jamais faire planter le cycle
        pass

    if not en_attente_envoi and not en_attente_action:
        print("Rien n'attend d'action humaine pour le moment.")


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="Manager : orchestre qualification + lecture email")
    parser.add_argument("--dry-run", action="store_true", help="simule tout le cycle sans API Claude ni Gmail")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
