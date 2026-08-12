"""
Agent LinkedIn — VALIDATION HUMAINE OBLIGATOIRE, envoi via GetSales.io.

Rôle : pour chaque prospect qualifié avec un lien LinkedIn, crée/met à jour
le lead correspondant dans GetSales, affiche le message prévu, et n'envoie
QUE si tu valides explicitement à l'invite.

Volontairement plus simple que l'agent email : pas de lecture ni de
classification des réponses — tu gères ça toi-même directement dans
GetSales/LinkedIn, comme demandé.

Usage :
    python3 -m agents.linkedin_agent                 # revue interactive, envoi réel après validation
    python3 -m agents.linkedin_agent --dry-run        # simule tout sans GetSales
    python3 -m agents.linkedin_agent --limit 10       # limite le nombre traité
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import database as db  # noqa: E402
from integrations import getsales_client  # noqa: E402

TEMPLATE_PATH = Path(__file__).parent.parent / "config" / "template_linkedin.yaml"

# Limite de sécurité par défaut si --limit n'est pas fourni : on évite
# qu'un lancement irréfléchi parte sur des centaines de prospects d'un
# coup (risque de restriction de compte LinkedIn — c'était déjà le point
# d'attention discuté au tout début du projet).
LIMITE_PAR_DEFAUT = 25


class SafeDict(dict):
    """Permet .format_map() sans planter si une variable du template est
    absente du prospect — elle reste affichée telle quelle."""

    def __missing__(self, key):
        return "{" + key + "}"


def load_template(path: Path = TEMPLATE_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def render(template_str: str, prospect: dict) -> str:
    return template_str.format_map(SafeDict(prospect))


def afficher_brouillon(prospect: dict, message: str) -> None:
    print("\n" + "=" * 70)
    print(f"À : {prospect.get('prenom', '')} {prospect.get('nom', '')} "
          f"({prospect.get('poste', '')} chez {prospect.get('entreprise', '')})")
    print(f"LinkedIn : {prospect.get('linkedin_url', '')}")
    print("-" * 70)
    print(message)
    print("=" * 70)


def demander_validation(dry_run: bool) -> str:
    """Retourne 'oui', 'non' ou 'quitter'. En --dry-run, auto-valide."""
    if dry_run:
        print("[DRY-RUN] validation automatique (oui) pour tester le flux, rien n'est réellement envoyé")
        return "oui"
    reponse = input("Envoyer ce message LinkedIn ? [o]ui / [n]on / [q]uitter : ").strip().lower()
    if reponse in ("o", "oui", "y", "yes"):
        return "oui"
    if reponse in ("q", "quit", "quitter"):
        return "quitter"
    return "non"


def run(dry_run: bool = False, limit: int | None = None) -> None:
    template = load_template()
    if "À REMPLIR" in template.get("premier_message", ""):
        print(
            "⚠️  config/template_linkedin.yaml n'a pas encore été personnalisé.\n"
            "   Tu vas voir un message placeholder si tu continues.\n",
            file=sys.stderr,
        )

    prospects = [p for p in db.list_prospects(statut="qualifie") if p.get("linkedin_url")]

    if limit:
        prospects = prospects[:limit]
    elif len(prospects) > LIMITE_PAR_DEFAUT:
        print(
            f"⚠️  {len(prospects)} prospects qualifiés trouvés — traitement limité "
            f"aux {LIMITE_PAR_DEFAUT} premiers par prudence (montée en charge "
            f"progressive). Utilise --limit pour changer ce chiffre.\n"
        )
        prospects = prospects[:LIMITE_PAR_DEFAUT]

    if not prospects:
        print("Aucun prospect qualifié avec un lien LinkedIn en attente.")
        return

    envoyes, refuses = 0, 0
    for p in prospects:
        message = render(template["premier_message"], p)
        note = render(template["note_connexion"], p) if template.get("note_connexion") else None

        afficher_brouillon(p, message)
        decision = demander_validation(dry_run)
        if decision == "quitter":
            print("Arrêt demandé.")
            break
        if decision == "non":
            print(f"  [{p['id']}] passé, reste 'qualifie' pour une prochaine revue.")
            refuses += 1
            continue

        try:
            if dry_run:
                lead_uuid = "dry-run-lead-uuid"
            else:
                lead = getsales_client.upsert_lead(p, note_connexion=note, premier_message=message)
                lead_uuid = lead["uuid"]
                db.set_getsales_lead_uuid(p["id"], lead_uuid)
                getsales_client.send_message(lead_uuid, message)

            db.update_statut(p["id"], "contacte")
            db.add_interaction(p["id"], "linkedin_envoye", message)
            envoyes += 1
            print(f"  [{p['id']}] ✅ envoyé, statut -> contacte")
        except Exception as exc:  # noqa: BLE001 - continuer sur les autres prospects
            print(f"  [{p['id']}] ERREUR d'envoi : {exc}", file=sys.stderr)

    print(f"\n{envoyes} envoyé(s), {refuses} passé(s).")


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="Agent LinkedIn (validation humaine obligatoire, via GetSales)")
    parser.add_argument("--dry-run", action="store_true", help="simule tout sans GetSales ni confirmation reelle")
    parser.add_argument("--limit", type=int, default=None, help="ne traiter que les N premiers prospects qualifiés")
    args = parser.parse_args()

    db.init_db()
    try:
        run(dry_run=args.dry_run, limit=args.limit)
    except RuntimeError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)
