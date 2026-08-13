"""
Interface web locale — pour tout piloter par clic plutôt que par le Terminal.

Usage :
    python3 -m dashboard.app
Puis ouvre http://127.0.0.1:5001 dans ton navigateur.

Cette app tourne UNIQUEMENT en local (127.0.0.1) — elle n'est jamais
exposée sur le réseau, donc pas de risque à ce que quelqu'un d'autre y
accède depuis l'extérieur.

Pages :
    /            tableau de bord (funnel, liste, actions rapides)
    /ajouter     ajouter des prospects (formulaire ou import CSV)
    /envoi       rédiger et envoyer les emails, un par un, avec validation
    /parametres  ICP, ton des emails, test de connexion Gmail
"""
from __future__ import annotations

import io
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import yaml
from flask import Flask, abort, flash, redirect, render_template, request, url_for

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents import email_reader, email_sender, qualification  # noqa: E402
from db import database as db  # noqa: E402
from integrations import gmail_client  # noqa: E402
from scripts import import_csv  # noqa: E402

app = Flask(__name__)
app.secret_key = "prospection-sammpo-local"  # app locale mono-utilisateur, pas d'enjeu de sécurité ici

STATUTS = ["nouveau", "qualifie", "disqualifie", "contacte", "repondu", "rdv", "perdu", "desinscrit"]

# Brouillons d'emails générés en attente de validation, gardés en mémoire
# le temps de la session (perdus si le serveur redémarre — pas grave,
# il suffit de re-générer, aucune donnée métier n'est perdue).
_BROUILLONS: dict[int, dict] = {}


def _executer_avec_capture(fn) -> str:
    """Exécute fn() en capturant tout ce qu'elle affiche (print, erreurs),
    pour l'afficher tel quel dans l'interface plutôt que dans un terminal."""
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer), redirect_stderr(buffer):
            fn()
    except Exception as exc:  # noqa: BLE001 - on veut afficher l'erreur, pas planter la page
        buffer.write(f"\n❌ ERREUR : {exc}\n")
    return buffer.getvalue() or "(aucune sortie)"


def _lignes(texte: str) -> list[str]:
    """Transforme un textarea (une valeur par ligne) en liste, sans lignes vides."""
    return [ligne.strip() for ligne in texte.splitlines() if ligne.strip()]


def _cle_api_manquante() -> bool:
    import os

    return not os.environ.get("ANTHROPIC_API_KEY")


# ---------------------------------------------------------------- dashboard

@app.route("/")
def index():
    db.init_db()
    filtre = request.args.get("statut") or None
    counts = {s: len(db.list_prospects(statut=s)) for s in STATUTS}
    total = sum(counts.values())
    prospects = db.list_prospects(statut=filtre) if filtre else db.list_prospects()
    return render_template(
        "index.html", counts=counts, total=total, prospects=prospects,
        statuts=STATUTS, filtre_actif=filtre, actif="dashboard",
    )


@app.route("/prospect/<int:prospect_id>")
def detail(prospect_id: int):
    db.init_db()
    prospect = db.get_prospect(prospect_id)
    if not prospect:
        abort(404)
    interactions = db.list_interactions(prospect_id)
    return render_template("prospect.html", prospect=prospect, interactions=interactions, actif="dashboard")


# ---------------------------------------------------------------- actions rapides

@app.route("/actions/qualifier", methods=["POST"])
def action_qualifier():
    if _cle_api_manquante():
        flash("ANTHROPIC_API_KEY n'est pas définie dans .env — impossible de qualifier.", "erreur")
        return redirect(url_for("index"))
    log = _executer_avec_capture(lambda: qualification.run(dry_run=False))
    return render_template("resultat.html", titre="Résultat de la qualification", log=log, actif="dashboard")


@app.route("/actions/verifier-emails", methods=["POST"])
def action_verifier_emails():
    log = _executer_avec_capture(lambda: email_reader.run(dry_run=False, test_connexion=False))
    return render_template("resultat.html", titre="Résultat de la vérification des emails", log=log, actif="dashboard")


# ---------------------------------------------------------------- ajouter des prospects

@app.route("/ajouter")
def ajouter():
    return render_template("ajouter.html", actif="ajouter")


@app.route("/ajouter/manuel", methods=["POST"])
def ajouter_manuel():
    data = {
        "prenom": request.form.get("prenom", ""),
        "nom": request.form.get("nom", ""),
        "poste": request.form.get("poste", ""),
        "entreprise": request.form.get("entreprise", ""),
        "secteur": request.form.get("secteur", ""),
        "taille_entreprise": request.form.get("taille", ""),
        "email": request.form.get("email") or None,
        "linkedin_url": request.form.get("linkedin") or None,
        "notes": request.form.get("notes") or None,
        "source": "interface",
    }
    try:
        prospect_id = db.add_prospect(data)
        flash(f"Prospect ajouté (id {prospect_id}).", "succes")
    except Exception as exc:  # noqa: BLE001
        flash(f"Erreur lors de l'ajout : {exc}", "erreur")
    return redirect(url_for("index"))


@app.route("/ajouter/csv", methods=["POST"])
def ajouter_csv():
    fichier = request.files.get("fichier")
    if not fichier or fichier.filename == "":
        flash("Aucun fichier sélectionné.", "erreur")
        return redirect(url_for("ajouter"))

    chemin_temp = Path(tempfile.gettempdir()) / f"import_{fichier.filename}"
    fichier.save(chemin_temp)
    log = _executer_avec_capture(lambda: import_csv.import_csv(str(chemin_temp)))
    chemin_temp.unlink(missing_ok=True)
    return render_template("resultat.html", titre="Résultat de l'import CSV", log=log, actif="ajouter")


# ---------------------------------------------------------------- envoi email

@app.route("/envoi")
def envoi():
    db.init_db()
    prospects = [p for p in db.list_prospects(statut="qualifie") if p.get("email")]
    for p in prospects:
        p["brouillon"] = _BROUILLONS.get(p["id"])
    return render_template("envoi.html", prospects=prospects, actif="envoi")


@app.route("/envoi/<int:prospect_id>/generer", methods=["POST"])
def envoi_generer(prospect_id: int):
    if _cle_api_manquante():
        flash("ANTHROPIC_API_KEY n'est pas définie — impossible de générer un brouillon.", "erreur")
        return redirect(url_for("envoi"))

    prospect = db.get_prospect(prospect_id)
    if not prospect:
        abort(404)
    try:
        icp = email_sender.load_icp()
        brief = email_sender.load_brief()
        _BROUILLONS[prospect_id] = email_sender.redact_email(prospect, icp, brief)
    except Exception as exc:  # noqa: BLE001
        flash(f"Erreur lors de la génération du brouillon : {exc}", "erreur")
    return redirect(url_for("envoi"))


@app.route("/envoi/<int:prospect_id>/envoyer", methods=["POST"])
def envoi_envoyer(prospect_id: int):
    prospect = db.get_prospect(prospect_id)
    brouillon = _BROUILLONS.get(prospect_id)
    if not prospect or not brouillon:
        flash("Pas de brouillon en attente pour ce prospect.", "erreur")
        return redirect(url_for("envoi"))

    try:
        service = gmail_client.get_service()
        gmail_client.send_message(service, prospect["email"], brouillon["objet"], brouillon["corps"])
        db.update_statut(prospect_id, "contacte")
        db.add_interaction(prospect_id, "email_envoye", f"Objet: {brouillon['objet']}")
        _BROUILLONS.pop(prospect_id, None)
        flash(f"Email envoyé à {prospect['prenom']} {prospect['nom']}.", "succes")
    except Exception as exc:  # noqa: BLE001
        flash(f"Erreur lors de l'envoi : {exc}", "erreur")
    return redirect(url_for("envoi"))


@app.route("/envoi/<int:prospect_id>/passer", methods=["POST"])
def envoi_passer(prospect_id: int):
    _BROUILLONS.pop(prospect_id, None)
    flash("Brouillon écarté — ce prospect reste 'qualifié' pour une prochaine revue.", "info")
    return redirect(url_for("envoi"))


# ---------------------------------------------------------------- paramètres

@app.route("/parametres")
def parametres():
    icp = qualification.load_icp()
    brief = email_sender.load_brief()
    return render_template("parametres.html", icp=icp, brief=brief, actif="parametres")


@app.route("/parametres/icp", methods=["POST"])
def parametres_icp():
    icp = qualification.load_icp()
    icp.setdefault("produit", {})
    icp.setdefault("cible", {})
    icp["produit"]["nom"] = request.form.get("produit_nom", "")
    icp["produit"]["description"] = request.form.get("produit_description", "")
    icp["produit"]["proposition_de_valeur"] = request.form.get("proposition_valeur", "")
    icp["cible"]["secteurs"] = _lignes(request.form.get("secteurs", ""))
    icp["cible"]["taille_entreprise"] = _lignes(request.form.get("taille_entreprise", ""))
    icp["cible"]["postes"] = _lignes(request.form.get("postes", ""))
    icp["cible"]["signaux_achat"] = _lignes(request.form.get("signaux_achat", ""))
    icp["exclusions"] = _lignes(request.form.get("exclusions", ""))
    try:
        icp["seuil_qualification"] = int(request.form.get("seuil", 60))
    except ValueError:
        icp["seuil_qualification"] = 60

    with open(qualification.ICP_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(icp, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    flash("ICP mis à jour.", "succes")
    return redirect(url_for("parametres"))


@app.route("/parametres/brief", methods=["POST"])
def parametres_brief():
    try:
        longueur = int(request.form.get("longueur", 150))
    except ValueError:
        longueur = 150

    brief = {
        "ton": request.form.get("ton", ""),
        "longueur_max_mots": longueur,
        "structure_attendue": request.form.get("structure", ""),
        "signature": request.form.get("signature", ""),
        "mention_obligatoire": request.form.get("mention", ""),
    }
    with open(email_sender.BRIEF_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(brief, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    flash("Ton des emails mis à jour.", "succes")
    return redirect(url_for("parametres"))


@app.route("/parametres/tester-gmail", methods=["POST"])
def parametres_tester_gmail():
    try:
        service = gmail_client.get_service()
        messages = gmail_client.search_messages(service, query="", max_results=3)
        flash(f"Connexion Gmail OK — {len(messages)} message(s) récent(s) trouvé(s).", "succes")
    except Exception as exc:  # noqa: BLE001
        flash(f"Erreur de connexion Gmail : {exc}", "erreur")
    return redirect(url_for("parametres"))


if __name__ == "__main__":
    db.init_db()
    print("Interface disponible sur http://127.0.0.1:5001")
    app.run(debug=False, port=5001)
