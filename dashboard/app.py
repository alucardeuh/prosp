"""
Interface web locale — pour tout piloter par clic plutôt que par le Terminal.

Usage :
    python3 -m dashboard.app
Puis ouvre http://127.0.0.1:5001 dans ton navigateur.

Cette app tourne UNIQUEMENT en local (127.0.0.1) — elle n'est jamais
exposée sur le réseau.

Pages :
    /            pipeline, recherche/tri, actions rapides
    /prospect/N  fiche éditable + historique
    /envoi       brouillons en masse (quota journalier), revue, édition, envoi
    /relances    prospects sans réponse à relancer (même flux de validation)
    /stats       envois par semaine, taux de réponse, conversion
    /parametres  profils (SAMMPO / médical...), ICP, ton, réglages, Gmail
    /ajouter     formulaire ou import CSV

Toutes les actions longues (appels API) tournent en arrière-plan : la page
ne gèle plus jamais, une barre de progression suit l'avancement en direct.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

# Chargé avant les imports suivants : les agents lisent CLAUDE_MODEL dès
# leur import (voir agents/qualification.py), donc .env doit déjà être en
# place à ce moment-là, pas seulement plus bas dans `if __name__ == "__main__"`.
load_dotenv()

from flask import (
    Flask, abort, flash, jsonify, redirect, render_template, request, url_for,
)

sys.path.insert(0, str(Path(__file__).parent.parent))
import profils  # noqa: E402
from agents import email_reader, email_sender, qualification  # noqa: E402
from dashboard import jobs  # noqa: E402
from db import database as db  # noqa: E402
from integrations import gmail_client  # noqa: E402

app = Flask(__name__)
app.secret_key = "prospection-locale"  # app locale mono-utilisateur

STATUTS = ["nouveau", "qualifie", "disqualifie", "contacte", "repondu", "rdv", "perdu", "desinscrit"]

LIBELLES_STATUT = {
    "nouveau": "Nouveau", "qualifie": "Qualifié", "disqualifie": "Disqualifié",
    "contacte": "Contacté", "repondu": "A répondu", "rdv": "RDV",
    "perdu": "Perdu", "desinscrit": "Désinscrit",
}

# Initialisation UNE SEULE FOIS au démarrage du process (import de ce module),
# plutôt qu'à chaque requête via before_request : schema.sql et la migration
# des profils n'ont aucune raison de retourner sur le disque à chaque clic.
db.init_db()
profils.init_profils()


@app.context_processor
def _contexte_global():
    profil = db.profil_actif()
    counts = db.counts_par_statut(profil=profil)
    limite = int(db.get_reglage("limite_envois_jour") or 50)
    return {
        "profil_actif": profil,
        "tous_profils": profils.list_profils(),
        "statuts": STATUTS,
        "libelles": LIBELLES_STATUT,
        "nb_a_envoyer": db.count_qualifies_avec_email(profil),
        "nb_a_relancer": len(_relances_dues(profil)),
        "nb_repondu": counts.get("repondu", 0),
        "envois_jour": db.envois_du_jour(),
        "limite_jour": limite,
    }


def _relances_dues(profil: str) -> list[dict]:
    delai = int(db.get_reglage("delai_relance_jours") or 7)
    max_r = int(db.get_reglage("max_relances") or 2)
    return db.prospects_a_relancer(profil, delai, max_r)


def _cle_api_manquante() -> bool:
    return not os.environ.get("ANTHROPIC_API_KEY")


def _quota_restant() -> int:
    limite = int(db.get_reglage("limite_envois_jour") or 50)
    return max(0, limite - db.envois_du_jour())


# ================================================================ pages

@app.route("/")
def index():
    profil = db.profil_actif()
    filtre = request.args.get("statut") or None
    tri = request.args.get("tri", "date_creation")
    ordre = request.args.get("ordre", "desc")
    counts = db.counts_par_statut(profil=profil)
    prospects = db.list_prospects(statut=filtre, profil=profil, tri=tri, ordre=ordre)
    return render_template(
        "index.html", counts=counts, total=sum(counts.values()), prospects=prospects,
        filtre_actif=filtre, tri=tri, ordre=ordre, actif="dashboard",
        cle_api_manquante=_cle_api_manquante(),
    )


@app.route("/prospect/<int:prospect_id>")
def detail(prospect_id: int):
    prospect = db.get_prospect(prospect_id)
    if not prospect:
        abort(404)
    interactions = db.list_interactions(prospect_id)
    brouillon = db.get_brouillon(prospect_id)
    return render_template("prospect.html", prospect=prospect,
                           interactions=interactions, brouillon=brouillon, actif="dashboard")


@app.route("/envoi")
def envoi():
    profil = db.profil_actif()
    prospects = [p for p in db.list_prospects(statut="qualifie", profil=profil, tri="score_qualification", ordre="desc")
                 if p.get("email")]
    brouillons = db.list_brouillons()
    for p in prospects:
        p["brouillon"] = brouillons.get(p["id"])
    avec = [p for p in prospects if p["brouillon"]]
    sans = [p for p in prospects if not p["brouillon"]]
    return render_template("envoi.html", avec_brouillon=avec, sans_brouillon=sans,
                           quota_restant=_quota_restant(), actif="envoi")


@app.route("/relances")
def relances():
    profil = db.profil_actif()
    dus = _relances_dues(profil)
    brouillons = db.list_brouillons()
    for p in dus:
        b = brouillons.get(p["id"])
        p["brouillon"] = b if (b and b["type"] == "relance") else None
    avec = [p for p in dus if p["brouillon"]]
    sans = [p for p in dus if not p["brouillon"]]
    return render_template("relances.html", avec_brouillon=avec, sans_brouillon=sans,
                           quota_restant=_quota_restant(),
                           delai=int(db.get_reglage("delai_relance_jours") or 7),
                           max_relances=int(db.get_reglage("max_relances") or 2),
                           actif="relances")


@app.route("/stats")
def stats():
    profil = db.profil_actif()
    globales = db.stats_globales(profil)
    semaines = db.stats_envois_par_semaine(8)
    max_semaine = max([s["envois"] + s["relances"] for s in semaines], default=0)
    return render_template("stats.html", g=globales, semaines=semaines,
                           max_semaine=max_semaine, actif="stats")


@app.route("/ajouter")
def ajouter():
    return render_template("ajouter.html", actif="ajouter")


@app.route("/parametres")
def parametres():
    profil = db.profil_actif()
    icp = profils.load_icp(profil)
    brief = profils.load_brief(profil)
    reglages = {
        "limite_envois_jour": db.get_reglage("limite_envois_jour"),
        "delai_relance_jours": db.get_reglage("delai_relance_jours"),
        "max_relances": db.get_reglage("max_relances"),
        "max_recherches_web": db.get_reglage("max_recherches_web"),
    }
    return render_template("parametres.html", icp=icp, brief=brief,
                           reglages=reglages, actif="parametres")


# ================================================================ jobs (API)

@app.route("/api/jobs/<job_id>")
def api_job(job_id: str):
    job = jobs.get_job(job_id)
    if not job:
        return jsonify({"erreur": "job inconnu"}), 404
    return jsonify(job)


@app.route("/api/jobs/actif")
def api_job_actif():
    return jsonify(jobs.job_actif() or {})


@app.route("/api/jobs/<job_id>/annuler", methods=["POST"])
def api_annuler_job(job_id: str):
    ok = jobs.demander_annulation(job_id)
    if not ok:
        return jsonify({"erreur": "Job introuvable ou déjà terminé."}), 404
    return jsonify({"ok": True, "message": "Annulation demandée — arrêt avant le prochain élément."})


@app.route("/api/jobs/qualifier", methods=["POST"])
def api_qualifier():
    if _cle_api_manquante():
        return jsonify({"erreur": "ANTHROPIC_API_KEY n'est pas définie dans .env."}), 400
    profil = db.profil_actif()
    icp = profils.load_icp(profil)
    nouveaux = db.list_prospects(statut="nouveau", profil=profil)
    if not nouveaux:
        return jsonify({"erreur": "Aucun prospect au statut « nouveau » à qualifier."}), 400

    def traiter(p, log):
        resultat = qualification.qualifier_un(p, icp)
        etat = "✅ qualifié" if resultat["qualifie"] else "— non qualifié"
        return f"{p.get('prenom','')} {p.get('nom','')} ({p.get('entreprise','')}) : {etat}, score {resultat['score']}"

    try:
        job_id = jobs.lancer(f"Qualification de {len(nouveaux)} prospect(s)", nouveaux, traiter)
    except RuntimeError as exc:
        return jsonify({"erreur": str(exc)}), 409
    return jsonify({"job_id": job_id})


@app.route("/api/jobs/generer-brouillons", methods=["POST"])
def api_generer_brouillons():
    """Génère en masse les brouillons manquants (initiaux ou relances),
    plafonné au quota d'envois restant du jour — inutile de payer des
    brouillons qu'on ne pourra pas envoyer aujourd'hui."""
    if _cle_api_manquante():
        return jsonify({"erreur": "ANTHROPIC_API_KEY n'est pas définie dans .env."}), 400
    donnees = request.get_json(silent=True) or {}
    type_ = donnees.get("type", "initial")
    profil = db.profil_actif()
    icp = profils.load_icp(profil)
    brief = profils.load_brief(profil)
    brouillons = db.list_brouillons()

    if type_ == "relance":
        candidats = [p for p in _relances_dues(profil) if p["id"] not in brouillons]
    else:
        candidats = [p for p in db.list_prospects(statut="qualifie", profil=profil,
                                                  tri="score_qualification", ordre="desc")
                     if p.get("email") and p["id"] not in brouillons]

    ids = donnees.get("ids")
    if ids:
        candidats = [p for p in candidats if p["id"] in ids]

    quota = _quota_restant()
    deja_prets = len(brouillons)
    plafond = max(0, quota - deja_prets)
    if plafond <= 0:
        return jsonify({"erreur": "Quota d'envois du jour déjà couvert par les brouillons existants."}), 400
    candidats = candidats[:plafond]
    if not candidats:
        return jsonify({"erreur": "Aucun brouillon à générer."}), 400

    def traiter(p, log):
        email_sender.generer_brouillon(p, icp, brief, type_=type_)
        return f"✍️ Brouillon prêt : {p.get('prenom','')} {p.get('nom','')} ({p.get('entreprise','')})"

    libelle = "relance(s)" if type_ == "relance" else "brouillon(s)"
    try:
        job_id = jobs.lancer(f"Rédaction de {len(candidats)} {libelle}", candidats, traiter)
    except RuntimeError as exc:
        return jsonify({"erreur": str(exc)}), 409
    return jsonify({"job_id": job_id})


@app.route("/api/prospects/<int:prospect_id>/generer", methods=["POST"])
def api_generer_un(prospect_id: int):
    if _cle_api_manquante():
        return jsonify({"erreur": "ANTHROPIC_API_KEY n'est pas définie dans .env."}), 400
    prospect = db.get_prospect(prospect_id)
    if not prospect:
        return jsonify({"erreur": "Prospect introuvable."}), 404
    donnees = request.get_json(silent=True) or {}
    type_ = donnees.get("type", "initial")
    profil = prospect.get("profil") or db.profil_actif()
    icp = profils.load_icp(profil)
    brief = profils.load_brief(profil)

    def traiter(p, log):
        email_sender.generer_brouillon(p, icp, brief, type_=type_)
        return f"✍️ Brouillon prêt : {p.get('prenom','')} {p.get('nom','')}"

    try:
        job_id = jobs.lancer(f"Rédaction pour {prospect.get('prenom','')} {prospect.get('nom','')}",
                             [prospect], traiter)
    except RuntimeError as exc:
        return jsonify({"erreur": str(exc)}), 409
    return jsonify({"job_id": job_id})


@app.route("/api/jobs/verifier-emails", methods=["POST"])
def api_verifier_emails():
    if _cle_api_manquante():
        return jsonify({"erreur": "ANTHROPIC_API_KEY n'est pas définie dans .env."}), 400
    prospects = db.list_prospects_avec_email()
    if not prospects:
        return jsonify({"erreur": "Aucun prospect avec une adresse email en base."}), 400

    # La collecte Gmail se fait dans le job (elle peut être longue elle aussi).
    def traiter(etape, log):
        service = gmail_client.get_service()
        paires = email_reader.collecter_nouveaux_emails(service, prospects)
        if not paires:
            return "Aucune nouvelle réponse trouvée dans Gmail."
        log(f"{len(paires)} nouvelle(s) réponse(s) à classer...")
        for prospect, email in paires:
            try:
                resultat = email_reader.traiter_email(prospect, email)
                marqueur = "🔴 " if resultat["categorie"] == "desinscription" else ""
                log(f"{marqueur}{prospect.get('prenom','')} {prospect.get('nom','')} : "
                    f"{resultat['categorie']} — {resultat['raison']}")
            except Exception as exc:  # noqa: BLE001
                log(f"❌ [{prospect['id']}] {exc}")
        return None

    try:
        job_id = jobs.lancer("Vérification des réponses email", ["scan"], traiter)
    except RuntimeError as exc:
        return jsonify({"erreur": str(exc)}), 409
    return jsonify({"job_id": job_id})


# ================================================================ prospects (API)

@app.route("/api/prospects/<int:prospect_id>/envoyer", methods=["POST"])
def api_envoyer(prospect_id: int):
    """Enregistre les éventuelles modifications du brouillon, puis envoie."""
    donnees = request.get_json(silent=True) or {}
    brouillon = db.get_brouillon(prospect_id)
    if brouillon and "objet" in donnees and "corps" in donnees:
        objet = (donnees.get("objet") or "").strip()
        corps = (donnees.get("corps") or "").strip()
        if not objet or not corps:
            return jsonify({"erreur": "L'objet et le corps ne peuvent pas être vides."}), 400
        db.set_brouillon(prospect_id, objet, corps, type_=brouillon["type"])
    try:
        brouillon = email_sender.envoyer_brouillon(prospect_id)
    except ValueError as exc:
        return jsonify({"erreur": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001 - erreur Gmail
        return jsonify({"erreur": f"Erreur Gmail : {exc}"}), 500
    prospect = db.get_prospect(prospect_id)
    return jsonify({"ok": True,
                    "message": f"Email envoyé à {prospect['prenom']} {prospect['nom']}.",
                    "quota_restant": _quota_restant()})


@app.route("/api/prospects/<int:prospect_id>/brouillon", methods=["POST"])
def api_sauver_brouillon(prospect_id: int):
    donnees = request.get_json(silent=True) or {}
    brouillon = db.get_brouillon(prospect_id)
    if not brouillon:
        return jsonify({"erreur": "Pas de brouillon pour ce prospect."}), 404
    objet = (donnees.get("objet") or "").strip()
    corps = (donnees.get("corps") or "").strip()
    if not objet or not corps:
        return jsonify({"erreur": "L'objet et le corps ne peuvent pas être vides."}), 400
    db.set_brouillon(prospect_id, objet, corps, type_=brouillon["type"])
    return jsonify({"ok": True, "message": "Brouillon enregistré."})


@app.route("/api/prospects/<int:prospect_id>/passer", methods=["POST"])
def api_passer(prospect_id: int):
    db.delete_brouillon(prospect_id)
    return jsonify({"ok": True, "message": "Brouillon écarté — le prospect reste dans la file."})


@app.route("/api/prospects/<int:prospect_id>/statut", methods=["POST"])
def api_statut(prospect_id: int):
    donnees = request.get_json(silent=True) or {}
    statut = donnees.get("statut")
    if statut not in STATUTS:
        return jsonify({"erreur": "Statut invalide."}), 400
    prospect = db.get_prospect(prospect_id)
    if not prospect:
        return jsonify({"erreur": "Prospect introuvable."}), 404
    db.update_statut(prospect_id, statut)
    db.add_interaction(prospect_id, "statut_manuel",
                       f"Statut changé manuellement : {prospect['statut']} -> {statut}")
    return jsonify({"ok": True, "message": f"Statut : {LIBELLES_STATUT[statut]}."})


@app.route("/api/prospects/<int:prospect_id>/champs", methods=["POST"])
def api_champs(prospect_id: int):
    if not db.get_prospect(prospect_id):
        return jsonify({"erreur": "Prospect introuvable."}), 404
    donnees = request.get_json(silent=True) or {}
    try:
        db.update_prospect(prospect_id, donnees)
    except Exception as exc:  # noqa: BLE001 - ex : doublon linkedin_url
        return jsonify({"erreur": str(exc)}), 400
    return jsonify({"ok": True, "message": "Fiche enregistrée."})


@app.route("/api/prospects/<int:prospect_id>/note", methods=["POST"])
def api_note(prospect_id: int):
    """Ajoute une note horodatée à l'historique (différent du champ notes de
    la fiche : ici c'est le journal de suivi, ex. compte-rendu d'appel)."""
    donnees = request.get_json(silent=True) or {}
    texte = (donnees.get("texte") or "").strip()
    if not texte:
        return jsonify({"erreur": "Note vide."}), 400
    db.add_interaction(prospect_id, "note", texte)
    return jsonify({"ok": True, "message": "Note ajoutée."})


@app.route("/api/prospects/<int:prospect_id>", methods=["DELETE"])
def api_supprimer(prospect_id: int):
    db.delete_prospect(prospect_id)
    return jsonify({"ok": True, "message": "Prospect supprimé."})


# ================================================================ profils & paramètres

@app.route("/api/profil", methods=["POST"])
def api_changer_profil():
    donnees = request.get_json(silent=True) or {}
    profil = donnees.get("profil")
    if profil not in profils.list_profils():
        return jsonify({"erreur": "Profil inconnu."}), 400
    db.set_reglage("profil_actif", profil)
    return jsonify({"ok": True})


@app.route("/parametres/nouveau-profil", methods=["POST"])
def parametres_nouveau_profil():
    nom = request.form.get("nom", "")
    try:
        identifiant = profils.creer_profil(nom)
        db.set_reglage("profil_actif", identifiant)
        flash(f"Profil « {identifiant} » créé et activé — remplis son ICP ci-dessous.", "succes")
    except ValueError as exc:
        flash(str(exc), "erreur")
    return redirect(url_for("parametres"))


def _lignes(texte: str) -> list[str]:
    return [ligne.strip() for ligne in texte.splitlines() if ligne.strip()]


@app.route("/parametres/icp", methods=["POST"])
def parametres_icp():
    profil = db.profil_actif()
    icp = profils.load_icp(profil)
    icp.setdefault("produit", {})
    icp.setdefault("cible", {})
    icp["produit"]["nom"] = request.form.get("produit_nom", "")
    icp["produit"]["description"] = request.form.get("produit_description", "")
    icp["produit"]["proposition_de_valeur"] = request.form.get("proposition_valeur", "")
    icp["cible"]["secteurs"] = _lignes(request.form.get("secteurs", ""))
    icp["cible"]["taille_entreprise"] = _lignes(request.form.get("taille_entreprise", ""))
    icp["cible"]["postes"] = _lignes(request.form.get("postes", ""))
    icp["cible"]["zones_geographiques"] = _lignes(request.form.get("zones", ""))
    icp["cible"]["signaux_achat"] = _lignes(request.form.get("signaux_achat", ""))
    icp["exclusions"] = _lignes(request.form.get("exclusions", ""))
    try:
        icp["seuil_qualification"] = int(request.form.get("seuil", 60))
    except ValueError:
        icp["seuil_qualification"] = 60
    profils.save_icp(profil, icp)
    flash(f"ICP du profil « {profil} » mis à jour.", "succes")
    return redirect(url_for("parametres"))


@app.route("/parametres/brief", methods=["POST"])
def parametres_brief():
    profil = db.profil_actif()
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
    profils.save_brief(profil, brief)
    flash(f"Ton des emails du profil « {profil} » mis à jour.", "succes")
    return redirect(url_for("parametres"))


@app.route("/parametres/reglages", methods=["POST"])
def parametres_reglages():
    for cle, defaut, plafond in (
        ("limite_envois_jour", 50, None),
        ("delai_relance_jours", 7, None),
        ("max_relances", 2, None),
        ("max_recherches_web", 3, 5),
    ):
        try:
            valeur = max(0, int(request.form.get(cle, defaut)))
        except ValueError:
            valeur = defaut
        if plafond is not None:
            valeur = min(valeur, plafond)
        db.set_reglage(cle, str(valeur))
    flash("Réglages enregistrés.", "succes")
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


# ================================================================ ajout

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
        "profil": db.profil_actif(),
    }
    try:
        db.add_prospect(data)
        flash(f"{data['prenom']} {data['nom']} ajouté au profil « {data['profil']} ».", "succes")
        return redirect(url_for("index"))
    except Exception as exc:  # noqa: BLE001
        flash(f"Erreur lors de l'ajout : {exc}", "erreur")
        return redirect(url_for("ajouter"))


@app.route("/ajouter/csv", methods=["POST"])
def ajouter_csv():
    fichier = request.files.get("fichier")
    if not fichier or fichier.filename == "":
        flash("Aucun fichier sélectionné.", "erreur")
        return redirect(url_for("ajouter"))

    import csv as csv_module
    import io

    profil = db.profil_actif()
    ajoutes, ignores = 0, []
    try:
        # io.TextIOWrapper(fichier.stream, ...) plante sous Python < 3.11 :
        # le flux interne de Flask (SpooledTemporaryFile) n'implémente
        # .readable() que depuis 3.11, ce que TextIOWrapper exige. On lit
        # donc les octets puis on décode nous-mêmes plutôt que d'envelopper
        # le flux — un CSV de prospects ne pèse jamais assez lourd pour que
        # ça pose un problème de mémoire.
        contenu = fichier.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("Le fichier ne semble pas être un CSV encodé en UTF-8 — "
              "ré-enregistre-le en UTF-8 puis réessaie.", "erreur")
        return redirect(url_for("ajouter"))

    try:
        for row in csv_module.DictReader(io.StringIO(contenu)):
            donnees = {k.strip(): v for k, v in row.items() if k and v}
            donnees["profil"] = profil
            donnees.setdefault("source", "import_csv")
            try:
                db.add_prospect(donnees)
                ajoutes += 1
            except Exception:  # noqa: BLE001 - doublon linkedin_url le plus souvent
                ignores.append(row.get("nom") or row.get("email") or "?")
    except Exception as exc:  # noqa: BLE001
        flash(f"Import impossible : {exc}", "erreur")
        return redirect(url_for("ajouter"))

    message = f"{ajoutes} prospect(s) importé(s) dans « {profil} »."
    if ignores:
        message += f" {len(ignores)} ignoré(s) (doublons) : {', '.join(ignores[:5])}{'...' if len(ignores) > 5 else ''}."
    flash(message, "succes" if ajoutes else "erreur")
    return redirect(url_for("index"))


if __name__ == "__main__":
    # .env chargé et base/profils initialisés plus haut, au niveau du module
    # (nécessaire dès l'import, pas seulement en lancement direct).
    print("Interface disponible sur http://127.0.0.1:5001")
    # threaded=True : indispensable pour que la page puisse interroger la
    # progression pendant qu'un job tourne dans un autre thread.
    app.run(debug=False, port=5001, threaded=True)
