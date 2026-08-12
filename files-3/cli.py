"""
Petite CLI pour manipuler la base sans écrire de SQL à la main.

Usage :
    python -m scripts.cli add --prenom Jean --nom Dupont --poste "Head of Sales" \\
        --entreprise Acme --secteur SaaS --taille "50-200" --linkedin "https://..." \\
        --email jean@acme.com

    python -m scripts.cli list
    python -m scripts.cli list --statut qualifie
    python -m scripts.cli show 1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import database as db  # noqa: E402


def cmd_add(args: argparse.Namespace) -> None:
    prospect_id = db.add_prospect(
        {
            "prenom": args.prenom,
            "nom": args.nom,
            "poste": args.poste,
            "entreprise": args.entreprise,
            "secteur": args.secteur,
            "taille_entreprise": args.taille,
            "linkedin_url": args.linkedin,
            "email": args.email,
            "source": args.source,
            "notes": args.notes,
        }
    )
    print(f"Prospect ajouté avec l'id {prospect_id} (statut: nouveau)")


def cmd_list(args: argparse.Namespace) -> None:
    prospects = db.list_prospects(statut=args.statut)
    if not prospects:
        print("Aucun prospect trouvé.")
        return
    for p in prospects:
        score = p.get("score_qualification")
        score_str = f" score={score}" if score is not None else ""
        print(f"[{p['id']}] {p.get('prenom','')} {p.get('nom','')} - "
              f"{p.get('entreprise','')} - {p['statut']}{score_str}")


def cmd_show(args: argparse.Namespace) -> None:
    p = db.get_prospect(args.id)
    if not p:
        print(f"Aucun prospect avec l'id {args.id}")
        return
    for k, v in p.items():
        print(f"{k}: {v}")


def main() -> None:
    parser = argparse.ArgumentParser(description="CLI de gestion des prospects")
    sub = parser.add_subparsers(dest="commande", required=True)

    p_add = sub.add_parser("add", help="ajouter un prospect")
    p_add.add_argument("--prenom", default="")
    p_add.add_argument("--nom", default="")
    p_add.add_argument("--poste", default="")
    p_add.add_argument("--entreprise", default="")
    p_add.add_argument("--secteur", default="")
    p_add.add_argument("--taille", default="")
    p_add.add_argument("--linkedin", default=None)
    p_add.add_argument("--email", default=None)
    p_add.add_argument("--source", default="manuel")
    p_add.add_argument("--notes", default=None)
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="lister les prospects")
    p_list.add_argument("--statut", default=None,
                         choices=["nouveau", "qualifie", "disqualifie", "contacte",
                                  "repondu", "rdv", "perdu", "desinscrit"])
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="détail d'un prospect")
    p_show.add_argument("id", type=int)
    p_show.set_defaults(func=cmd_show)

    args = parser.parse_args()
    db.init_db()
    args.func(args)


if __name__ == "__main__":
    main()
