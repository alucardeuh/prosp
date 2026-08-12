"""
Dashboard de suivi — lecture seule, local uniquement, aucune écriture en base.

Usage :
    python3 -m dashboard.app
Puis ouvre http://127.0.0.1:5001 dans ton navigateur.

Note : le port 5001 est utilisé plutôt que 5000 parce que macOS réserve le
5000 pour AirPlay Receiver depuis Monterey — Flask sur 5000 y renvoie
souvent une erreur 403 qui n'a rien à voir avec le code.
"""
from __future__ import annotations

import sys
from pathlib import Path

from flask import Flask, abort, render_template, request

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import database as db  # noqa: E402

app = Flask(__name__)

STATUTS = ["nouveau", "qualifie", "disqualifie", "contacte", "repondu", "rdv", "perdu", "desinscrit"]


@app.route("/")
def index():
    db.init_db()
    filtre = request.args.get("statut") or None
    counts = {s: len(db.list_prospects(statut=s)) for s in STATUTS}
    total = sum(counts.values())
    prospects = db.list_prospects(statut=filtre) if filtre else db.list_prospects()
    return render_template(
        "index.html",
        counts=counts,
        total=total,
        prospects=prospects,
        statuts=STATUTS,
        filtre_actif=filtre,
    )


@app.route("/prospect/<int:prospect_id>")
def detail(prospect_id: int):
    db.init_db()
    prospect = db.get_prospect(prospect_id)
    if not prospect:
        abort(404)
    interactions = db.list_interactions(prospect_id)
    return render_template("prospect.html", prospect=prospect, interactions=interactions)


if __name__ == "__main__":
    db.init_db()
    print("Dashboard disponible sur http://127.0.0.1:5001")
    app.run(debug=True, port=5001)
