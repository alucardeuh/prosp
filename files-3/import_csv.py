"""
Import en masse depuis un CSV (export Clay, Apollo, PhantomBuster, etc.)

Le CSV doit avoir des en-têtes parmi : prenom, nom, poste, entreprise,
secteur, taille_entreprise, linkedin_url, email. Les colonnes absentes
sont simplement ignorées, pas besoin d'avoir tout.

Usage :
    python -m scripts.import_csv chemin/vers/export.csv
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import database as db  # noqa: E402


def import_csv(chemin: str) -> None:
    db.init_db()
    ajoutes, erreurs = 0, 0
    with open(chemin, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                db.add_prospect({k: v for k, v in row.items() if v}, )
                ajoutes += 1
            except Exception as exc:  # doublon linkedin_url le plus souvent
                print(f"  ignoré ({row.get('nom', '?')}) : {exc}", file=sys.stderr)
                erreurs += 1
    print(f"Import terminé : {ajoutes} ajoutés, {erreurs} ignorés.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage : python -m scripts.import_csv chemin/vers/fichier.csv")
        sys.exit(1)
    import_csv(sys.argv[1])
