"""
Agent d'envoi — VALIDATION HUMAINE OBLIGATOIRE, pas de mode automatique.

Rôle : pour chaque prospect qualifié (statut='qualifie'), génère l'email à
partir de config/template_prospection.yaml, te l'affiche, et n'envoie QUE
si tu valides explicitement à l'invite. C'est un choix de conception, pas
une limitation technique à contourner plus tard sans y réfléchir — voir la
discussion sur le cadre légal (RGPD / art. L34-5 CPCE) qu'on a eue au
début du projet.

Usage :
    python3 -m agents.email_sender                 # revue interactive, envoi réel après validation
    python3 -m agents.email_sender --dry-run        # simule tout le flux sans Gmail ni confirmation humaine
    python3 -m agents.email_sender --limit 5        # ne traite que les 5 premiers prospects qualifiés
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import database as db  # noqa: E402
from integrations import gmail_client  # noqa: E402

TEMPLATE_PATH = Path(__file__).parent.parent / "config" / "template_prospection.yaml"


class SafeDict(dict):
    """Permet .format_map() sans planter si une variable du template est
    absente du prospect — elle reste affichée telle quelle (ex: {secteur})
    plutôt que de lever un KeyError en pleine revue."""

    def __missing__(self, key):
        return "{" + key + "}"


def load_template(path: Path = TEMPLATE_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def render(template_str: str, prospect: dict) -> str:
    return template_str.format_map(SafeDict(prospect))


def afficher_brouillon(prospect: dict, objet: str, corps: str) -> None:
    print("\n" + "=" * 70)
    print(f"À : {prospect.get('prenom', '')} {prospect.get('nom', '')} <{prospect.get('email', '')}>")
    print(f"Objet : {objet}")
    print("-" * 70)
    print(corps)
    print("=" * 70)


def demander_validation(dry_run: bool) -> str:
    """Retourne 'oui', 'non' ou 'quitter'. En --dry-run, auto-valide pour
    permettre de tester tout le flux sans interaction humaine ni Gmail."""
    if dry_run:
        print("[DRY-RUN] validation automatique (oui) pour tester le flux, aucun email réel envoyé")
        return "oui"
    reponse = input("Envoyer cet email ? [o]ui / [n]on / [q]uitter : ").strip().lower()
    if reponse in ("o", "oui", "y", "yes"):
        return "oui"
    if reponse in ("q", "quit", "quitter"):
        return "quitter"
    return "non"


def run(dry_run: bool = False, limit: int | None = None) -> None:
    template = load_template()
    if "À REMPLIR" in template.get("objet", "") or "À REMPLIR" in template.get("corps", ""):
        print(
            "⚠️  config/template_prospection.yaml n'a pas encore été personnalisé.\n"
            "   Tu vas voir un email placeholder si tu continues.\n",
            file=sys.stderr,
        )

    prospects = db.list_prospects(statut="qualifie")
    if limit:
        prospects = prospects[:limit]

    if not prospects:
        print("Aucun prospect qualifié en attente d'envoi.")
        return

    service = None if dry_run else gmail_client.get_service()
    envoyes, refuses = 0, 0

    for p in prospects:
        if not p.get("email"):
            print(f"  [{p['id']}] {p.get('prenom', '')} {p.get('nom', '')} : pas d'email, ignoré.", file=sys.stderr)
            continue

        objet = render(template["objet"], p)
        corps = render(template["corps"], p)
        afficher_brouillon(p, objet, corps)

        decision = demander_validation(dry_run)
        if decision == "quitter":
            print("Arrêt demandé.")
            break
        if decision == "non":
            print(f"  [{p['id']}] passé, reste 'qualifie' pour une prochaine revue.")
            refuses += 1
            continue

        try:
            if not dry_run:
                gmail_client.send_message(service, p["email"], objet, corps)
            db.update_statut(p["id"], "contacte")
            db.add_interaction(p["id"], "email_envoye", f"Objet: {objet}")
            envoyes += 1
            print(f"  [{p['id']}] ✅ envoyé, statut -> contacte")
        except Exception as exc:  # noqa: BLE001 - continuer sur les autres prospects
            print(f"  [{p['id']}] ERREUR d'envoi : {exc}", file=sys.stderr)

    print(f"\n{envoyes} envoyé(s), {refuses} passé(s).")


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="Agent d'envoi (validation humaine obligatoire)")
    parser.add_argument("--dry-run", action="store_true", help="simule tout sans Gmail ni confirmation humaine")
    parser.add_argument("--limit", type=int, default=None, help="ne traiter que les N premiers prospects qualifiés")
    args = parser.parse_args()

    db.init_db()
    try:
        run(dry_run=args.dry_run, limit=args.limit)
    except FileNotFoundError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)
