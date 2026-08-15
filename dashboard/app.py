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
import re
import sys
import tempfile
import json
from pathlib import Path

from dotenv import load_dotenv

# Chargé avant les imports suivants : ANTHROPIC_API_KEY doit être disponible
# dès que possible. Les modèles (CLAUDE_MODEL, CLAUDE_MODEL_RAPIDE) sont lus
# à chaque appel API par les agents, pas seulement à l'import — un changement
# fait depuis Paramètres prend donc effet sans redémarrer l'app.
load_dotenv()

ENV_PATH = Path(__file__).parent.parent / ".env"


def _lire_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    valeurs = {}
    for ligne in ENV_PATH.read_text().splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, valeur = ligne.partition("=")
        valeurs[cle.strip()] = valeur.strip()
    return valeurs


def _definir_env(cle: str, valeur: str) -> None:
    """Écrit/remplace UNE variable dans .env sans toucher au reste du
    fichier (commentaires, autres variables) — reconnaît aussi une ligne
    commentée existante (ex : '# CLAUDE_MODEL=...' dans .env.example) et la
    décommente plutôt que d'en ajouter une deuxième en double. Applique le
    changement immédiatement au process en cours : pas besoin de redémarrer
    l'app pour qu'il prenne effet."""
    lignes = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    trouve = False
    for i, ligne in enumerate(lignes):
        nu = ligne.strip().lstrip("#").strip()
        if nu.startswith(f"{cle}="):
            lignes[i] = f"{cle}={valeur}"
            trouve = True
            break
    if not trouve:
        lignes.append(f"{cle}={valeur}")
    ENV_PATH.write_text("\n".join(lignes) + "\n")
    os.environ[cle] = valeur


from flask import (
    Flask, abort, flash, jsonify, redirect, render_template, request, url_for,
)

sys.path.insert(0, str(Path(__file__).parent.parent))
import email_verification  # noqa: E402
import profils  # noqa: E402
from agents import email_reader, email_sender, qualification  # noqa: E402
from dashboard import jobs, planificateur  # noqa: E402
from db import database as db  # noqa: E402
from integrations import gmail_client, hubspot_client  # noqa: E402

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


def _reparer_collisions_champs_perso() -> None:
    """Migration ponctuelle : une version antérieure ne bloquait pas la
    création d'un champ personnalisé portant le même nom qu'un champ fixe
    (ex : 'poste'), ce qui faisait dérouter silencieusement les valeurs
    saisies dans champs_perso au lieu de la vraie colonne. Répare toute
    collision déjà présente, sans action manuelle. Ne fait plus rien une
    fois les collisions nettoyées (idempotent, coût négligeable au démarrage)."""
    for profil in profils.list_profils():
        champs = profils.load_champs(profil)
        en_collision = [c for c in champs if c["nom"] in profils.NOMS_RESERVES]
        if not en_collision:
            continue
        for c in en_collision:
            n = db.reparer_collision_champ_perso(c["nom"], profil)
            if n:
                print(f"Migration : {n} prospect(s) du profil « {profil} » corrigé(s) "
                      f"(champ « {c['nom']} » mal routé récupéré).", file=sys.stderr)
        profils.save_champs(profil, [c for c in champs if c["nom"] not in profils.NOMS_RESERVES])
        print(f"Migration : champ(s) en collision retiré(s) du profil « {profil} » : "
              f"{', '.join(c['nom'] for c in en_collision)}.", file=sys.stderr)


_reparer_collisions_champs_perso()


def _migrer_connexions_globales_vers_sammpo() -> None:
    """Migration ponctuelle : avant cette version, Gmail et HubSpot étaient
    connectés une seule fois pour toute l'app. Maintenant que chaque profil
    a ses propres connexions, une connexion globale déjà en place est
    rattachée au profil 'sammpo' (le tout premier profil, très probablement
    celui déjà connecté) plutôt que d'obliger à tout reconnecter à zéro."""
    ancien_secret = gmail_client.CREDS_DIR / "client_secret.json"
    ancien_token = gmail_client.CREDS_DIR / "token.json"
    if ancien_secret.exists() and not gmail_client.chemin_client_secret("sammpo").exists():
        gmail_client.dossier_profil("sammpo").mkdir(parents=True, exist_ok=True)
        ancien_secret.rename(gmail_client.chemin_client_secret("sammpo"))
        print("Migration : identifiants Gmail rattachés au profil « sammpo ».", file=sys.stderr)
    if ancien_token.exists() and not gmail_client.chemin_token("sammpo").exists():
        gmail_client.dossier_profil("sammpo").mkdir(parents=True, exist_ok=True)
        ancien_token.rename(gmail_client.chemin_token("sammpo"))
        print("Migration : session Gmail rattachée au profil « sammpo ».", file=sys.stderr)

    ancien_token_hubspot = db.get_reglage("hubspot_token")
    if ancien_token_hubspot and not db.get_reglage("hubspot_token__sammpo"):
        db.set_reglage("hubspot_token__sammpo", ancien_token_hubspot)
        db.set_reglage("hubspot_token", "")
        print("Migration : token HubSpot rattaché au profil « sammpo ».", file=sys.stderr)


_migrer_connexions_globales_vers_sammpo()

# Le planificateur tourne en tâche de fond dès le démarrage : vérifie toutes
# les 30s s'il y a un envoi programmé arrivé à échéance (voir planificateur.py).
planificateur.demarrer()


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


def _statut_gmail(profil: str) -> dict:
    if not gmail_client.chemin_client_secret(profil).exists():
        return {"etat": "sans_identifiants", "libelle": "Identifiants OAuth manquants"}
    if not gmail_client.chemin_token(profil).exists():
        return {"etat": "pret", "libelle": "Prêt à autoriser"}
    return {"etat": "connecte", "libelle": "Connecté"}


def _statut_hubspot(profil: str) -> dict:
    token = db.get_reglage(f"hubspot_token__{profil}")
    if not token:
        return {"etat": "non_connecte", "libelle": "Non connecté"}
    return {"etat": "token_enregistre", "libelle": "Token enregistré"}


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
    champs_perso = profils.load_champs(prospect.get("profil") or db.profil_actif())
    valeurs_perso = json.loads(prospect.get("champs_perso") or "{}")
    return render_template("prospect.html", prospect=prospect,
                           interactions=interactions, brouillon=brouillon,
                           champs_perso=champs_perso, valeurs_perso=valeurs_perso, actif="dashboard")


@app.route("/envoi")
def envoi():
    profil = db.profil_actif()

    # Brouillons déjà créés : n'importe quel statut de qualification (écrire
    # à la main n'exige pas d'être "qualifie" — seule la génération IA en
    # masse ci-dessous reste liée aux qualifiés). Une seule requête jointe,
    # déjà scopée au profil et filtrée sur l'email — pas de boucle avec un
    # get_prospect() par brouillon. Séparés en deux : actifs (à traiter
    # maintenant) et mis de côté ("Passer" ne supprime plus rien, juste
    # range ailleurs pour ne pas perdre le texte).
    prospects_avec_brouillon = db.list_prospects_avec_brouillon(profil)
    actifs = [p for p in prospects_avec_brouillon if not p["brouillon"].get("mis_de_cote")]
    de_cote = [p for p in prospects_avec_brouillon if p["brouillon"].get("mis_de_cote")]
    actifs.sort(key=lambda p: p.get("score_qualification") or 0, reverse=True)
    de_cote.sort(key=lambda p: p.get("score_qualification") or 0, reverse=True)
    ids_avec_brouillon = {p["id"] for p in actifs + de_cote}

    sans = [p for p in db.list_prospects(statut="qualifie", profil=profil, tri="score_qualification", ordre="desc")
            if p.get("email") and p["id"] not in ids_avec_brouillon]

    selection = db.list_prospects_pour_selection(profil)
    postes = sorted({p["poste"] for p in selection if p.get("poste")})

    # Même logique que /parametres pour retrouver le nom du niveau
    # correspondant à la valeur numérique enregistrée — sans ça, le
    # sélecteur de cette page ignorait le réglage de Paramètres et
    # présélectionnait toujours "normal" en dur, quoi qu'on ait configuré.
    valeur_recherche = int(db.get_reglage("max_recherches_web") or 3)
    niveau_recherche_defaut = next(
        (nom for nom, val in email_sender.NIVEAUX_RECHERCHE.items() if val == valeur_recherche),
        "normal",
    )

    return render_template("envoi.html", avec_brouillon=actifs, mis_de_cote=de_cote, sans_brouillon=sans,
                           quota_restant=_quota_restant(), actif="envoi",
                           selection=selection, postes=postes, statuts=STATUTS,
                           niveaux_recherche=list(email_sender.NIVEAUX_RECHERCHE.keys()),
                           niveau_recherche_defaut=niveau_recherche_defaut,
                           modeles=profils.load_modeles(profil))


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
    tokens = db.stats_tokens(profil)
    return render_template("stats.html", g=globales, semaines=semaines,
                           max_semaine=max_semaine, tokens=tokens, actif="stats")


@app.route("/ajouter")
def ajouter():
    profil = db.profil_actif()
    return render_template("ajouter.html", actif="ajouter", champs_perso=profils.load_champs(profil),
                           champs_cibles_import=CHAMPS_CIBLES_IMPORT)


@app.route("/parametres")
def parametres():
    profil = db.profil_actif()
    icp = profils.load_icp(profil)
    brief = profils.load_brief(profil)
    valeur_recherche = int(db.get_reglage("max_recherches_web") or 3)
    # Retrouve le nom du niveau correspondant à la valeur stockée (approche
    # par défaut si la valeur ne correspond à aucun niveau nommé standard).
    niveau_defaut = next(
        (nom for nom, val in email_sender.NIVEAUX_RECHERCHE.items() if val == valeur_recherche),
        "normal",
    )
    reglages = {
        "limite_envois_jour": db.get_reglage("limite_envois_jour"),
        "delai_relance_jours": db.get_reglage("delai_relance_jours"),
        "max_relances": db.get_reglage("max_relances"),
        "max_recherches_web": niveau_defaut,
        "niveau_reflexion": db.get_reglage("niveau_reflexion") or "desactive",
    }
    cle_anthropic = os.environ.get("ANTHROPIC_API_KEY", "")
    anthropic_config = {
        "cle_masquee": (cle_anthropic[:10] + "…" + cle_anthropic[-4:]) if len(cle_anthropic) > 14 else ("(définie)" if cle_anthropic else ""),
        "connecte": bool(cle_anthropic),
        "modele_redaction": os.environ.get("CLAUDE_MODEL", "claude-sonnet-5"),
        "modele_rapide": os.environ.get("CLAUDE_MODEL_RAPIDE", "claude-haiku-4-5-20251001"),
    }
    return render_template("parametres.html", icp=icp, brief=brief, reglages=reglages,
                           niveaux_recherche=email_sender.NIVEAUX_RECHERCHE,
                           statut_gmail=_statut_gmail(profil), statut_hubspot=_statut_hubspot(profil),
                           profil_connexions=profil, anthropic=anthropic_config,
                           actif="parametres")


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
        return jsonify({"erreur": "Clé API Anthropic non définie — renseigne-la dans Paramètres."}), 400
    profil = db.profil_actif()
    icp = profils.load_icp(profil)

    donnees = request.get_json(silent=True) or {}
    ids = donnees.get("ids")
    if ids:
        # Sélection sur mesure : n'importe quel statut, désinscrit exclu —
        # utile pour requalifier quelqu'un après une mise à jour de l'ICP
        # ou de sa fiche, pas seulement les tout nouveaux.
        a_qualifier = db.get_prospects(ids)
        a_qualifier = [p for p in a_qualifier if p and p.get("profil") == profil
                      and p["statut"] != "desinscrit"]
        if not a_qualifier:
            return jsonify({"erreur": "Aucun prospect à qualifier dans cette sélection."}), 400
    else:
        a_qualifier = db.list_prospects(statut="nouveau", profil=profil)
        if not a_qualifier:
            return jsonify({"erreur": "Aucun prospect au statut « nouveau » à qualifier."}), 400

    def traiter(p, log):
        resultat = qualification.qualifier_un(p, icp)
        etat = "✅ qualifié" if resultat["qualifie"] else "— non qualifié"
        return f"{p.get('prenom','')} {p.get('nom','')} ({p.get('entreprise','')}) : {etat}, score {resultat['score']}"

    try:
        job_id = jobs.lancer(f"Qualification de {len(a_qualifier)} prospect(s)", a_qualifier, traiter)
    except RuntimeError as exc:
        return jsonify({"erreur": str(exc)}), 409
    return jsonify({"job_id": job_id})


@app.route("/api/prospects/supprimer-selection", methods=["POST"])
def api_supprimer_selection():
    profil = db.profil_actif()
    donnees = request.get_json(silent=True) or {}
    ids = donnees.get("ids") or []
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return jsonify({"erreur": "Sélection invalide."}), 400
    n = db.delete_prospects(ids, profil)
    if n == 0:
        return jsonify({"erreur": "Rien à supprimer dans cette sélection."}), 400
    return jsonify({"ok": True, "message": f"{n} prospect(s) supprimé(s) définitivement."})


@app.route("/parametres/supprimer-tous-prospects", methods=["POST"])
def parametres_supprimer_tous_prospects():
    profil = db.profil_actif()
    confirmation = request.form.get("confirmation", "").strip()
    if confirmation != profil:
        flash(f"Suppression annulée — il fallait taper « {profil} » exactement pour confirmer.", "erreur")
        return redirect(url_for("parametres"))
    n = db.delete_tous_prospects(profil)
    flash(f"{n} prospect(s) du profil « {profil} » supprimé(s) définitivement.", "succes")
    return redirect(url_for("parametres"))


@app.route("/api/jobs/generer-brouillons", methods=["POST"])
def api_generer_brouillons():
    """Génère en masse les brouillons manquants (initiaux ou relances),
    plafonné au quota d'envois restant du jour — inutile de payer des
    brouillons qu'on ne pourra pas envoyer aujourd'hui.

    Deux modes :
    - automatique (pas de `ids`) : tous les qualifiés sans brouillon (email)
      ou toutes les relances dues sans brouillon (relance).
    - sur mesure (`ids` fourni) : exactement ces prospects, quel que soit
      leur statut de qualification — seul un désinscrit reste bloqué, dans
      tous les cas, à ce stade comme à l'envoi."""
    if _cle_api_manquante():
        return jsonify({"erreur": "Clé API Anthropic non définie — renseigne-la dans Paramètres."}), 400
    donnees = request.get_json(silent=True) or {}
    type_ = donnees.get("type", "initial")
    niveau_recherche = donnees.get("niveau_recherche")
    contexte_batch = (donnees.get("contexte_batch") or "").strip()
    profil = db.profil_actif()
    icp = profils.load_icp(profil)
    brief = profils.load_brief(profil)
    brouillons = db.list_brouillons()

    ids = donnees.get("ids")
    if ids:
        # Sélection sur mesure : n'importe quel statut, désinscrit exclu.
        # Scopé au profil ACTIF : la page /envoi ne liste que ses prospects,
        # mais si le profil change dans un autre onglet entre l'affichage et
        # le clic, les ids reçus peuvent appartenir à un autre profil — et
        # l'email serait alors rédigé avec l'ICP/le brief/la signature du
        # mauvais profil (fuite de contenu d'un profil vers les prospects
        # d'un autre). On refuse ces prospects plutôt que de mal les traiter.
        candidats = db.get_prospects(ids)
        candidats = [p for p in candidats if p and p.get("profil") == profil
                    and p["statut"] != "desinscrit"
                    and p.get("email") and p["id"] not in brouillons]
    elif type_ == "relance":
        candidats = [p for p in _relances_dues(profil) if p["id"] not in brouillons]
    else:
        candidats = [p for p in db.list_prospects(statut="qualifie", profil=profil,
                                                  tri="score_qualification", ordre="desc")
                     if p.get("email") and p["id"] not in brouillons]

    quota = _quota_restant()
    # Seuls les brouillons ACTIFS comptent contre le plafond : un brouillon
    # « mis de côté » est explicitement rangé pour plus tard, il ne sera pas
    # envoyé aujourd'hui — le compter revenait à ce que chaque brouillon
    # rangé ampute définitivement la capacité de génération quotidienne
    # (jusqu'à la bloquer complètement, y compris pour les AUTRES profils,
    # puisque les brouillons ne sont pas scopés par profil ici). Le quota
    # d'envoi restant, lui, reste bien global tous profils (une seule
    # réputation d'envoi) — c'est voulu, seul le décompte des brouillons
    # change.
    deja_prets = sum(1 for b in brouillons.values() if not b.get("mis_de_cote"))
    plafond = max(0, quota - deja_prets)
    if plafond <= 0:
        return jsonify({"erreur": "Quota d'envois du jour déjà couvert par les brouillons existants."}), 400
    tronque = len(candidats) > plafond
    candidats = candidats[:plafond]
    if not candidats:
        return jsonify({"erreur": "Aucun brouillon à générer."}), 400

    def traiter(p, log):
        email_sender.generer_brouillon(p, icp, brief, type_=type_,
                                       niveau_recherche=niveau_recherche, contexte_batch=contexte_batch)
        return f"✍️ Brouillon prêt : {p.get('prenom','')} {p.get('nom','')} ({p.get('entreprise','')})"

    def terminer(log):
        if tronque:
            return f"⚠️ Limité au quota du jour restant ({plafond}) — le reste de la sélection n'a pas été généré."
        return None

    libelle = "relance(s)" if type_ == "relance" else "brouillon(s)"
    try:
        job_id = jobs.lancer(f"Rédaction de {len(candidats)} {libelle}", candidats, traiter, terminer=terminer)
    except RuntimeError as exc:
        return jsonify({"erreur": str(exc)}), 409
    return jsonify({"job_id": job_id})


@app.route("/api/prospects/<int:prospect_id>/generer", methods=["POST"])
def api_generer_un(prospect_id: int):
    if _cle_api_manquante():
        return jsonify({"erreur": "Clé API Anthropic non définie — renseigne-la dans Paramètres."}), 400
    prospect = db.get_prospect(prospect_id)
    if not prospect:
        return jsonify({"erreur": "Prospect introuvable."}), 404
    donnees = request.get_json(silent=True) or {}
    type_ = donnees.get("type", "initial")
    niveau_recherche = donnees.get("niveau_recherche")
    contexte_batch = (donnees.get("contexte_batch") or "").strip()
    profil = prospect.get("profil") or db.profil_actif()
    icp = profils.load_icp(profil)
    brief = profils.load_brief(profil)

    def traiter(p, log):
        email_sender.generer_brouillon(p, icp, brief, type_=type_,
                                       niveau_recherche=niveau_recherche, contexte_batch=contexte_batch)
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
        return jsonify({"erreur": "Clé API Anthropic non définie — renseigne-la dans Paramètres."}), 400
    profil = db.profil_actif()
    prospects = db.list_prospects_avec_email(profil)
    if not prospects:
        # Deux situations très différentes derrière une liste vide :
        # list_prospects_avec_email ne retient que les prospects déjà
        # CONTACTÉS via l'outil (on ne peut pas avoir reçu une réponse à un
        # email jamais envoyé). Dire « aucun prospect avec email » à
        # quelqu'un qui en a 50 en base était trompeur et bloquant.
        avec_email = [p for p in db.list_prospects(profil=profil) if p.get("email")]
        if avec_email:
            return jsonify({"erreur": (
                "Aucun prospect de ce profil n'a encore été contacté depuis "
                "l'outil — le scan ne cherche que les réponses aux emails "
                "envoyés d'ici. Envoie d'abord un premier email (page Envoi), "
                "puis relance la vérification."
            )}), 400
        return jsonify({"erreur": "Aucun prospect avec une adresse email en base pour ce profil."}), 400

    # La collecte Gmail se fait dans le job (elle peut être longue elle aussi).
    def traiter(etape, log):
        service = gmail_client.get_service(profil)
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


def _substituer_variables(texte: str, prospect: dict) -> str:
    """Remplace les variables {{prenom}}/{{nom}}/{{entreprise}}/{{poste}}
    d'un modèle par les vraies valeurs du prospect — le seul 'moteur' de
    personnalisation en rédaction manuelle, volontairement simple (pas
    d'IA, donc pas de tokens consommés)."""
    remplacements = {
        "{{prenom}}": prospect.get("prenom") or "",
        "{{nom}}": prospect.get("nom") or "",
        "{{entreprise}}": prospect.get("entreprise") or "",
        "{{poste}}": prospect.get("poste") or "",
    }
    for cle, valeur in remplacements.items():
        texte = texte.replace(cle, valeur)
    return texte


@app.route("/api/prospects/<int:prospect_id>/brouillon-manuel", methods=["POST"])
def api_brouillon_manuel(prospect_id: int):
    """Crée un brouillon SANS appeler Claude — vierge, pré-rempli à partir
    d'un modèle d'email enregistré (variables substituées côté serveur), ou
    avec un objet/corps déjà prêts (le composeur d'/envoi fait la
    substitution côté client pour un aperçu immédiat, et envoie le résultat
    tel quel — modifiable avant l'envoi dans les deux cas).
    Zéro token consommé : tokens_entree/tokens_sortie restent à 0 par défaut."""
    prospect = db.get_prospect(prospect_id)
    if not prospect:
        return jsonify({"erreur": "Prospect introuvable."}), 404
    if prospect["statut"] == "desinscrit":
        return jsonify({"erreur": "Ce prospect s'est désinscrit."}), 400

    donnees = request.get_json(silent=True) or {}
    type_ = donnees.get("type", "initial")
    modele_index = donnees.get("modele_index")
    objet_fourni = donnees.get("objet")
    corps_fourni = donnees.get("corps")

    if objet_fourni is not None or corps_fourni is not None:
        objet = (objet_fourni or "").strip()
        corps = (corps_fourni or "").strip()
    elif modele_index is not None and modele_index != "":
        profil = prospect.get("profil") or db.profil_actif()
        modeles = profils.load_modeles(profil)
        try:
            modele = modeles[int(modele_index)]
        except (ValueError, IndexError):
            return jsonify({"erreur": "Modèle introuvable."}), 404
        objet = _substituer_variables(modele.get("objet", ""), prospect)
        corps = _substituer_variables(modele.get("corps", ""), prospect)
    else:
        objet, corps = "", ""

    db.set_brouillon(prospect_id, objet, corps, type_=type_)
    return jsonify({"ok": True, "message": "Brouillon créé — modifie-le puis envoie quand tu es prêt."})


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


@app.route("/api/prospects/<int:prospect_id>/programmer", methods=["POST"])
def api_programmer(prospect_id: int):
    brouillon = db.get_brouillon(prospect_id)
    if not brouillon:
        return jsonify({"erreur": "Pas de brouillon en attente pour ce prospect."}), 404
    donnees = request.get_json(silent=True) or {}
    date_envoi = (donnees.get("date_envoi") or "").strip() or None
    if date_envoi:
        try:
            from datetime import datetime
            datetime.fromisoformat(date_envoi)
        except ValueError:
            return jsonify({"erreur": "Date invalide."}), 400
    db.programmer_brouillon(prospect_id, date_envoi)
    message = f"Envoi programmé pour le {date_envoi.replace('T', ' à ')}." if date_envoi else "Programmation annulée."
    return jsonify({"ok": True, "message": message, "date_envoi": date_envoi})


@app.route("/api/prospects/<int:prospect_id>/passer", methods=["POST"])
def api_passer(prospect_id: int):
    db.mettre_brouillon_de_cote(prospect_id)
    return jsonify({"ok": True, "message": "Mis de côté — retrouvable dans l'onglet « Mis de côté »."})


@app.route("/api/prospects/<int:prospect_id>/reprendre", methods=["POST"])
def api_reprendre(prospect_id: int):
    db.reprendre_brouillon(prospect_id)
    return jsonify({"ok": True, "message": "Brouillon repris — de retour dans les brouillons actifs."})


@app.route("/api/prospects/<int:prospect_id>/supprimer-brouillon", methods=["POST"])
def api_supprimer_brouillon(prospect_id: int):
    db.delete_brouillon(prospect_id)
    return jsonify({"ok": True, "message": "Brouillon supprimé définitivement."})


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
    prospect = db.get_prospect(prospect_id)
    if not prospect:
        return jsonify({"erreur": "Prospect introuvable."}), 404
    donnees = request.get_json(silent=True) or {}
    noms_champs = {c["nom"] for c in profils.load_champs(prospect.get("profil") or db.profil_actif())}
    fixes = {k: v for k, v in donnees.items() if k not in noms_champs}
    perso = {k: v for k, v in donnees.items() if k in noms_champs}
    try:
        if fixes:
            db.update_prospect(prospect_id, fixes)
        if perso:
            db.update_champs_perso(prospect_id, perso)
    except Exception as exc:  # noqa: BLE001 - ex : doublon linkedin_url
        return jsonify({"erreur": str(exc)}), 400
    return jsonify({"ok": True, "message": "Fiche enregistrée."})


@app.route("/api/prospects/<int:prospect_id>/verifier-email", methods=["POST"])
def api_verifier_email(prospect_id: int):
    prospect = db.get_prospect(prospect_id)
    if not prospect:
        return jsonify({"erreur": "Prospect introuvable."}), 404
    if not prospect.get("email"):
        return jsonify({"erreur": "Ce prospect n'a pas d'adresse email."}), 400
    resultat = email_verification.verifier(prospect["email"])
    db.set_email_verifie(prospect_id, resultat["statut"])
    return jsonify({"ok": True, "statut": resultat["statut"], "raison": resultat["raison"]})


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
    for cle, defaut in (
        ("limite_envois_jour", 50),
        ("delai_relance_jours", 7),
        ("max_relances", 2),
    ):
        try:
            valeur = max(0, int(request.form.get(cle, defaut)))
        except ValueError:
            valeur = defaut
        db.set_reglage(cle, str(valeur))

    niveau = request.form.get("max_recherches_web", "normal")
    db.set_reglage("max_recherches_web", str(email_sender.NIVEAUX_RECHERCHE.get(niveau, 3)))

    niveau_reflexion = request.form.get("niveau_reflexion", "desactive")
    if niveau_reflexion not in email_sender.NIVEAUX_REFLEXION:
        niveau_reflexion = "desactive"
    db.set_reglage("niveau_reflexion", niveau_reflexion)

    flash("Réglages enregistrés.", "succes")
    return redirect(url_for("parametres"))


@app.route("/parametres/anthropic/cle", methods=["POST"])
def parametres_anthropic_cle():
    cle = request.form.get("cle", "").strip()
    if not cle:
        flash("Clé vide.", "erreur")
        return redirect(url_for("parametres"))
    _definir_env("ANTHROPIC_API_KEY", cle)
    flash("Clé API Anthropic enregistrée — active immédiatement, pas besoin de redémarrer.", "succes")
    return redirect(url_for("parametres"))


@app.route("/parametres/anthropic/deconnecter", methods=["POST"])
def parametres_anthropic_deconnecter():
    _definir_env("ANTHROPIC_API_KEY", "")
    flash("Clé API Anthropic retirée.", "succes")
    return redirect(url_for("parametres"))


@app.route("/parametres/anthropic/modeles", methods=["POST"])
def parametres_anthropic_modeles():
    modele = request.form.get("modele_redaction", "").strip()
    modele_rapide = request.form.get("modele_rapide", "").strip()
    if modele:
        _definir_env("CLAUDE_MODEL", modele)
    if modele_rapide:
        _definir_env("CLAUDE_MODEL_RAPIDE", modele_rapide)
    flash("Modèles enregistrés — actifs dès le prochain appel.", "succes")
    return redirect(url_for("parametres"))


@app.route("/parametres/tester-gmail", methods=["POST"])
def parametres_tester_gmail():
    profil = db.profil_actif()
    try:
        service = gmail_client.get_service(profil)
        messages = gmail_client.search_messages(service, query="", max_results=3)
        flash(f"Connexion Gmail OK (profil « {profil} ») — {len(messages)} message(s) récent(s) trouvé(s).", "succes")
    except Exception as exc:  # noqa: BLE001
        flash(f"Erreur de connexion Gmail : {exc}", "erreur")
    return redirect(url_for("parametres"))


@app.route("/parametres/gmail/identifiants", methods=["POST"])
def parametres_gmail_identifiants():
    """Upload direct du client_secret.json téléchargé depuis Google Cloud
    Console — évite d'avoir à le renommer et le déplacer soi-même dans
    credentials/<profil>/ via le Finder ou le Terminal. Propre au profil actif."""
    profil = db.profil_actif()
    fichier = request.files.get("fichier")
    if not fichier or fichier.filename == "":
        flash("Aucun fichier sélectionné.", "erreur")
        return redirect(url_for("parametres"))
    try:
        contenu = json.loads(fichier.read().decode("utf-8"))
        if "installed" not in contenu and "web" not in contenu:
            raise ValueError(
                "Ce fichier ne ressemble pas à un identifiant OAuth Google "
                "(section 'installed' ou 'web' introuvable)."
            )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        flash(f"Fichier invalide : {exc}", "erreur")
        return redirect(url_for("parametres"))

    gmail_client.dossier_profil(profil).mkdir(parents=True, exist_ok=True)
    gmail_client.chemin_client_secret(profil).write_text(json.dumps(contenu))
    flash(f"Identifiants enregistrés pour « {profil} » — clique sur « Connecter Gmail » pour autoriser l'accès.", "succes")
    return redirect(url_for("parametres"))


@app.route("/parametres/gmail/deconnecter", methods=["POST"])
def parametres_gmail_deconnecter():
    profil = db.profil_actif()
    gmail_client.chemin_token(profil).unlink(missing_ok=True)
    flash(f"Gmail déconnecté pour « {profil} » — reclique sur « Connecter Gmail » pour autoriser un autre compte.", "succes")
    return redirect(url_for("parametres"))


@app.route("/parametres/hubspot/token", methods=["POST"])
def parametres_hubspot_token():
    profil = db.profil_actif()
    token = request.form.get("token", "").strip()
    if not token:
        flash("Token vide.", "erreur")
        return redirect(url_for("parametres"))
    db.set_reglage(f"hubspot_token__{profil}", token)
    flash(f"Token HubSpot enregistré pour « {profil} » — teste la connexion pour vérifier qu'il fonctionne.", "succes")
    return redirect(url_for("parametres"))


@app.route("/parametres/hubspot/deconnecter", methods=["POST"])
def parametres_hubspot_deconnecter():
    profil = db.profil_actif()
    db.set_reglage(f"hubspot_token__{profil}", "")
    flash(f"HubSpot déconnecté pour « {profil} ».", "succes")
    return redirect(url_for("parametres"))


@app.route("/parametres/hubspot/tester", methods=["POST"])
def parametres_hubspot_tester():
    profil = db.profil_actif()
    token = db.get_reglage(f"hubspot_token__{profil}")
    if not token:
        flash("Aucun token HubSpot enregistré pour ce profil.", "erreur")
        return redirect(url_for("parametres"))
    try:
        resultat = hubspot_client.tester_connexion(token)
        suite = " (et bien d'autres)" if resultat["plus_de_contacts"] else ""
        flash(f"Connexion HubSpot OK — au moins {resultat['nb_contacts_page']} contact(s) trouvé(s){suite}.", "succes")
    except hubspot_client.ErreurHubSpot as exc:
        flash(f"Erreur HubSpot : {exc}", "erreur")
    return redirect(url_for("parametres"))


@app.route("/api/jobs/importer-hubspot", methods=["POST"])
def api_importer_hubspot():
    profil = db.profil_actif()
    token = db.get_reglage(f"hubspot_token__{profil}")
    if not token:
        return jsonify({"erreur": "Aucun token HubSpot enregistré pour ce profil."}), 400

    # Le champ hubspot_id sert au dédoublonnage (pas d'URL LinkedIn fiable
    # venant de HubSpot) — créé une seule fois, silencieusement s'il existe déjà.
    try:
        profils.ajouter_champ(profil, "hubspot_id", "HubSpot ID")
    except ValueError:
        pass  # déjà présent, rien à faire

    try:
        premiere_page, curseur = hubspot_client.lister_contacts(token, limit=100)
    except hubspot_client.ErreurHubSpot as exc:
        return jsonify({"erreur": str(exc)}), 400

    tous_contacts = list(premiere_page)
    # On récupère le reste des pages avant de lancer le job (pour connaître
    # le total et afficher une vraie progression) — HubSpot répond vite,
    # et un import perso reste de taille modeste.
    while curseur:
        try:
            page, curseur = hubspot_client.lister_contacts(token, after=curseur, limit=100)
        except hubspot_client.ErreurHubSpot as exc:
            return jsonify({"erreur": f"Import interrompu pendant la pagination : {exc}"}), 400
        tous_contacts.extend(page)
        if len(tous_contacts) >= 2000:  # garde-fou : pas d'import sans fin par erreur de config
            break

    if not tous_contacts:
        return jsonify({"erreur": "Aucun contact trouvé sur HubSpot."}), 400

    def traiter(contact, log):
        hs_id = contact.get("id")
        if hs_id and db.prospect_existe_par_champ_perso("hubspot_id", hs_id, profil):
            return None  # déjà importé, silencieux (pas la peine de logguer 500 lignes "déjà là")
        data = hubspot_client.contact_vers_prospect(contact)
        nom = f"{data.get('prenom','')} {data.get('nom','')}".strip() or "(sans nom)"
        try:
            db.add_prospect(data, champs_perso={"hubspot_id": hs_id} if hs_id else {})
            return f"➕ {nom}"
        except Exception as exc:  # noqa: BLE001 - doublon email/linkedin le plus probable
            return f"⏭️ {nom} ignoré ({exc})"

    try:
        job_id = jobs.lancer(f"Import HubSpot ({len(tous_contacts)} contact(s) à vérifier)",
                             tous_contacts, traiter)
    except RuntimeError as exc:
        return jsonify({"erreur": str(exc)}), 409
    return jsonify({"job_id": job_id})


# ================================================================ ajout

@app.route("/ajouter/manuel", methods=["POST"])
def ajouter_manuel():
    profil = db.profil_actif()
    data = {
        "prenom": request.form.get("prenom", ""),
        "nom": request.form.get("nom", ""),
        "poste": request.form.get("poste", ""),
        "entreprise": request.form.get("entreprise", ""),
        "secteur": request.form.get("secteur", ""),
        "taille_entreprise": request.form.get("taille", ""),
        "email": request.form.get("email") or None,
        "linkedin_url": request.form.get("linkedin") or None,
        "telephone": request.form.get("telephone") or None,
        "notes": request.form.get("notes") or None,
        "source": "interface",
        "profil": profil,
    }
    # Champs personnalisés : chaque <input name="champ_<nom>"> du formulaire,
    # généré dynamiquement d'après config/profils/<profil>/champs.yaml.
    noms_champs = {c["nom"] for c in profils.load_champs(profil)}
    champs_perso = {
        nom: request.form.get(f"champ_{nom}", "").strip()
        for nom in noms_champs
        if request.form.get(f"champ_{nom}", "").strip()
    }
    try:
        db.add_prospect(data, champs_perso=champs_perso)
        flash(f"{data['prenom']} {data['nom']} ajouté au profil « {data['profil']} ».", "succes")
        return redirect(url_for("index"))
    except Exception as exc:  # noqa: BLE001
        flash(f"Erreur lors de l'ajout : {exc}", "erreur")
        return redirect(url_for("ajouter"))


@app.route("/ajouter/champs", methods=["POST"])
def ajouter_champ_perso():
    profil = db.profil_actif()
    libelle = request.form.get("libelle", "")
    nom = request.form.get("nom") or libelle
    try:
        profils.ajouter_champ(profil, nom, libelle)
        flash(f"Champ « {libelle or nom} » ajouté.", "succes")
    except ValueError as exc:
        flash(str(exc), "erreur")
    return redirect(url_for("ajouter"))


@app.route("/modeles")
def modeles_page():
    profil = db.profil_actif()
    return render_template("modeles.html", modeles=profils.load_modeles(profil), actif="modeles")


@app.route("/modeles/ajouter", methods=["POST"])
def ajouter_modele_email():
    profil = db.profil_actif()
    titre = request.form.get("titre", "")
    objet = request.form.get("objet", "")
    corps = request.form.get("corps", "")
    try:
        profils.ajouter_modele(profil, titre, objet, corps)
        flash(f"Modèle « {titre} » ajouté.", "succes")
    except ValueError as exc:
        flash(str(exc), "erreur")
    return redirect(url_for("modeles_page"))


@app.route("/modeles/supprimer", methods=["POST"])
def supprimer_modele_email():
    profil = db.profil_actif()
    try:
        index = int(request.form.get("index", -1))
    except ValueError:
        index = -1
    profils.supprimer_modele(profil, index)
    flash("Modèle supprimé.", "succes")
    return redirect(url_for("modeles_page"))


@app.route("/ajouter/champs/supprimer", methods=["POST"])
def supprimer_champ_perso():
    profil = db.profil_actif()
    nom = request.form.get("nom", "")
    profils.supprimer_champ(profil, nom)
    flash("Champ supprimé (les valeurs déjà enregistrées restent en base, juste masquées).", "succes")
    return redirect(url_for("ajouter"))


# Alias de colonnes CSV reconnus (comparés après normalisation via
# profils._identifiant_sur, donc insensibles à la casse et aux accents) —
# couvre notamment les exports HubSpot en français. Sert de détection
# AUTOMATIQUE par défaut — ajustable colonne par colonne à l'import
# (voir /ajouter/csv/previsualiser), donc pas besoin de deviner tous les
# synonymes possibles dans toutes les langues (ex : "clinique" pour
# "entreprise" sur un annuaire médical).
ALIAS_COLONNES_CSV = {
    "prenom": "prenom",
    "nom": "nom",
    "email": "email", "e_mail": "email", "adresse_email": "email",
    "telephone": "telephone", "numero_de_telephone": "telephone", "tel": "telephone",
    "poste": "poste", "titre": "poste", "fonction": "poste", "job_title": "poste",
    "entreprise": "entreprise", "societe": "entreprise", "company": "entreprise",
    "company_name": "entreprise", "nom_de_l_entreprise": "entreprise",
    "linkedin": "linkedin_url", "linkedin_url": "linkedin_url", "url_linkedin": "linkedin_url",
    "secteur": "secteur", "industry": "secteur", "industrie": "secteur",
}

# Champs cibles proposés dans le sélecteur de correspondance à l'import —
# mêmes valeurs que celles utilisées comme cibles dans ALIAS_COLONNES_CSV,
# plus les deux options spéciales "ignorer" et "champ personnalisé".
CHAMPS_CIBLES_IMPORT = [
    ("prenom", "Prénom"), ("nom", "Nom"), ("email", "Email"), ("telephone", "Téléphone"),
    ("poste", "Poste"), ("entreprise", "Entreprise"), ("secteur", "Secteur"),
    ("linkedin_url", "LinkedIn"), ("champ_perso", "Champ personnalisé"), ("ignorer", "Ignorer cette colonne"),
]

# Titres/qualifications fréquents en tête d'un nom complet non séparé (ex :
# exports scrapés n'ayant qu'une seule colonne "Dr. Jonida Reveli" plutôt
# que prénom et nom distincts) — retirés avant de séparer prénom et nom.
TITRES_NOM_COMPLET = {"dr", "shk", "prof", "as", "m", "mme", "mr", "mlle"}


def _separer_prenom_nom(nom_complet: str) -> tuple[str, str]:
    """Sépare un nom complet ('Dr. Jonida Reveli') en (prénom, nom) —
    ('Jonida', 'Reveli') — quand un CSV n'a qu'une seule colonne de nom
    plutôt que prénom et nom distincts. Retire d'abord les titres usuels
    (Dr., Prof., As. pour Assistent...), garde le premier mot restant comme
    prénom, tout le reste comme nom. Repli sur (vide, texte original en
    entier) si un seul mot subsiste : impossible de séparer, mieux vaut
    tout garder dans nom que d'inventer un prénom."""
    mots = re.sub(r"\s+", " ", nom_complet.strip()).split(" ")
    while mots and mots[0].rstrip(".").lower() in TITRES_NOM_COMPLET:
        mots.pop(0)
    if len(mots) <= 1:
        return "", " ".join(mots)
    return mots[0], " ".join(mots[1:])

# Colonnes spéciales des exports HubSpot : un contact peut être associé à
# PLUSIEURS entreprises (listées séparées par ';'), et prendre juste la
# première donnerait parfois la mauvaise — l'ordre n'est pas garanti
# "principale en premier". On croise plutôt les IDs pour trouver la bonne.
_COL_ENTREPRISES = "associated_company"
_COL_ENTREPRISES_IDS = "associated_company_ids"
_COL_ID_ENTREPRISE_PRINCIPALE = "id_de_l_entreprise_principale_associee"
_COL_ID_HUBSPOT = "id_de_fiche_d_informations"


def _entreprise_principale_hubspot(row_norm: dict) -> str:
    """'Associated Company IDs' liste les IDs dans le même ordre que les
    noms dans 'Associated Company' ; 'ID de l'entreprise principale
    associée' indique lequel des deux (ou plus) est la bonne. Repli sur la
    première de la liste si le croisement échoue — au moins une valeur
    plutôt qu'aucune."""
    noms = row_norm.get(_COL_ENTREPRISES, "")
    if not noms:
        return ""
    liste_noms = [n.strip() for n in noms.split(";") if n.strip()]
    if not liste_noms:
        return ""
    id_principal = row_norm.get(_COL_ID_ENTREPRISE_PRINCIPALE, "").strip()
    ids = row_norm.get(_COL_ENTREPRISES_IDS, "")
    if id_principal and ids:
        liste_ids = [i.strip() for i in ids.split(";")]
        if id_principal in liste_ids:
            index = liste_ids.index(id_principal)
            if index < len(liste_noms):
                return liste_noms[index]
    return liste_noms[0]


def _mapper_ligne_csv(row: dict, profil: str, noms_champs_deja_crees: set,
                      correspondance: dict[str, str] | None = None) -> tuple[dict, dict]:
    """Transforme une ligne CSV brute (en-têtes libres, ex. export HubSpot
    en français ou annuaire scrapé) en (donnees_fixes, champs_perso) prêts
    pour add_prospect. correspondance, si fourni (colonne normalisée ->
    champ cible ou 'ignorer' ou 'champ_perso'), prend le pas sur la
    détection automatique — ajustable à l'import, voir
    /ajouter/csv/previsualiser. Rien n'est perdu par défaut : tout ce qui
    n'est pas reconnu comme champ fixe devient automatiquement un champ
    personnalisé (créé une seule fois par import, pas par ligne)."""
    correspondance = correspondance or {}
    row_norm, entete_origine = {}, {}
    for cle, valeur in row.items():
        if not cle or not valeur:
            continue
        norm = profils._identifiant_sur(cle)
        row_norm[norm] = valeur.strip()
        entete_origine[norm] = cle.strip()

    def _creer_champ_si_besoin(nom: str, libelle: str) -> None:
        if nom in noms_champs_deja_crees:
            return
        try:
            profils.ajouter_champ(profil, nom, libelle)
        except ValueError:
            pass  # déjà existant (colonne vue sur une ligne précédente, ou nom réservé) -> pas bloquant
        noms_champs_deja_crees.add(nom)

    donnees, champs_perso = {}, {}
    colonnes_traitees_a_part = {_COL_ENTREPRISES, _COL_ENTREPRISES_IDS,
                                _COL_ID_ENTREPRISE_PRINCIPALE, _COL_ID_HUBSPOT}

    for norm, valeur in row_norm.items():
        if norm in colonnes_traitees_a_part:
            continue
        cible = correspondance.get(norm)
        if cible == "ignorer":
            continue
        if cible and cible != "champ_perso":
            donnees[cible] = valeur
        elif cible == "champ_perso" or (not cible and norm not in ALIAS_COLONNES_CSV):
            _creer_champ_si_besoin(norm, entete_origine[norm])
            champs_perso[norm] = valeur
        else:
            donnees[ALIAS_COLONNES_CSV[norm]] = valeur

    if _COL_ID_HUBSPOT in row_norm:
        # Même nom de champ perso que l'import API HubSpot -> dédoublonnage
        # cohérent quelle que soit la méthode d'import utilisée.
        _creer_champ_si_besoin("hubspot_id", "HubSpot ID")
        champs_perso["hubspot_id"] = row_norm[_COL_ID_HUBSPOT]

    if "entreprise" not in donnees:
        entreprise = _entreprise_principale_hubspot(row_norm)
        if entreprise:
            donnees["entreprise"] = entreprise
        if ";" in row_norm.get(_COL_ENTREPRISES, ""):
            # Plusieurs entreprises associées : garde le détail complet en
            # plus de la principale retenue ci-dessus.
            _creer_champ_si_besoin("entreprises_associees", "Entreprises associées (HubSpot)")
            champs_perso["entreprises_associees"] = row_norm[_COL_ENTREPRISES]

    # Une seule colonne "nom" contenant le nom complet (fréquent sur les
    # annuaires scrapés, ex. mjeket.al : "Dr. Jonida Reveli" en une seule
    # colonne) plutôt que prénom et nom séparés -> sépare automatiquement
    # plutôt que de laisser prénom vide et tout jeter dans nom.
    if donnees.get("nom") and not donnees.get("prenom"):
        prenom_separe, nom_separe = _separer_prenom_nom(donnees["nom"])
        if prenom_separe:
            donnees["prenom"] = prenom_separe
            donnees["nom"] = nom_separe

    return donnees, champs_perso


import tempfile
import time
import uuid

# Fichiers CSV en attente de confirmation après prévisualisation — un
# dashboard local mono-utilisateur n'a pas besoin d'un vrai stockage de
# session, un dict en mémoire suffit (perdu si l'app redémarre entre les
# deux étapes, ce qui n'arrive jamais en usage normal). Valeur = (chemin,
# horodatage de création) — sert à purger les imports prévisualisés puis
# jamais confirmés (onglet fermé, "Annuler" oublié...), sans quoi le
# fichier temporaire ne serait jamais nettoyé.
_IMPORTS_EN_ATTENTE: dict[str, tuple[Path, float]] = {}
_DELAI_EXPIRATION_IMPORT = 30 * 60  # 30 minutes


def _nettoyer_import_en_attente(token: str) -> None:
    entree = _IMPORTS_EN_ATTENTE.pop(token, None)
    if entree and entree[0].exists():
        entree[0].unlink(missing_ok=True)


def _purger_imports_expires() -> None:
    """Nettoie les prévisualisations abandonnées depuis plus de 30 minutes
    (onglet fermé, page rechargée sans cliquer "Annuler" ni "Confirmer") —
    appelé au début de chaque nouvelle prévisualisation, pas besoin d'un
    vrai planificateur pour un ménage aussi ponctuel."""
    maintenant = time.time()
    expires = [tok for tok, (_, cree_le) in _IMPORTS_EN_ATTENTE.items()
              if maintenant - cree_le > _DELAI_EXPIRATION_IMPORT]
    for tok in expires:
        _nettoyer_import_en_attente(tok)


def _detection_auto_colonne(norm: str) -> str:
    if norm in ALIAS_COLONNES_CSV:
        return ALIAS_COLONNES_CSV[norm]
    return "champ_perso"


@app.route("/ajouter/csv/annuler", methods=["POST"])
def annuler_import_csv():
    donnees = request.get_json(silent=True) or {}
    _nettoyer_import_en_attente(donnees.get("token", ""))
    return jsonify({"ok": True})


@app.route("/ajouter/csv/previsualiser", methods=["POST"])
def previsualiser_csv():
    """Étape 1 : lit le fichier, détecte les colonnes et leur correspondance
    automatique, garde le fichier de côté (token) le temps que la personne
    ajuste si besoin. Rien n'est encore importé à ce stade."""
    _purger_imports_expires()
    fichier = request.files.get("fichier")
    if not fichier or fichier.filename == "":
        return jsonify({"erreur": "Aucun fichier sélectionné."}), 400

    import csv as csv_module
    import io

    try:
        contenu = fichier.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify({"erreur": "Le fichier ne semble pas être un CSV encodé en "
                                  "UTF-8 — ré-enregistre-le en UTF-8 puis réessaie."}), 400

    try:
        lignes = list(csv_module.DictReader(io.StringIO(contenu)))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"erreur": f"Fichier illisible : {exc}"}), 400
    if not lignes:
        return jsonify({"erreur": "Le fichier est vide."}), 400

    entetes = list(lignes[0].keys())
    premiere_ligne = lignes[0]
    colonnes_speciales = {_COL_ENTREPRISES, _COL_ENTREPRISES_IDS,
                          _COL_ID_ENTREPRISE_PRINCIPALE, _COL_ID_HUBSPOT}

    colonnes = []
    for entete in entetes:
        norm = profils._identifiant_sur(entete)
        colonnes.append({
            "brut": entete,
            "normalise": norm,
            "special": norm in colonnes_speciales,
            "cible_auto": "entreprise" if norm in (_COL_ENTREPRISES, _COL_ENTREPRISES_IDS,
                                                    _COL_ID_ENTREPRISE_PRINCIPALE)
                         else ("champ_perso" if norm == _COL_ID_HUBSPOT else _detection_auto_colonne(norm)),
            "exemple": (premiere_ligne.get(entete) or "")[:80],
        })

    # Sauvegarde temporaire du contenu décodé, retrouvé à l'étape de
    # confirmation via le token plutôt que de re-uploader le fichier.
    token = uuid.uuid4().hex
    chemin_temp = Path(tempfile.gettempdir()) / f"prosp_import_{token}.csv"
    chemin_temp.write_text(contenu, encoding="utf-8")
    _IMPORTS_EN_ATTENTE[token] = (chemin_temp, time.time())

    return jsonify({"ok": True, "token": token, "nb_lignes": len(lignes), "colonnes": colonnes})


@app.route("/ajouter/csv/confirmer", methods=["POST"])
def confirmer_csv():
    """Étape 2 : importe pour de bon, avec la correspondance éventuellement
    ajustée par colonne (sinon la détection automatique de l'étape 1)."""
    donnees_requete = request.get_json(silent=True) or {}
    token = donnees_requete.get("token", "")
    correspondance = donnees_requete.get("mapping") or {}

    entree = _IMPORTS_EN_ATTENTE.get(token)
    chemin_temp = entree[0] if entree else None
    if not chemin_temp or not chemin_temp.exists():
        return jsonify({"erreur": "Cette prévisualisation a expiré — réessaie l'import."}), 400

    import csv as csv_module

    profil = db.profil_actif()
    ajoutes, ignores = 0, []
    try:
        contenu = chemin_temp.read_text(encoding="utf-8")
        noms_champs_deja_crees = {c["nom"] for c in profils.load_champs(profil)}
        for row in csv_module.DictReader(contenu.splitlines()):
            donnees, champs_perso = _mapper_ligne_csv(row, profil, noms_champs_deja_crees, correspondance)
            donnees["profil"] = profil
            donnees.setdefault("source", "import_csv")
            nom_affiche = f"{donnees.get('prenom', '')} {donnees.get('nom', '')}".strip() or donnees.get("email") or "?"
            hubspot_id = champs_perso.get("hubspot_id", "")
            deja_present = db.prospect_existe_par_email(donnees.get("email", ""), profil) or (
                hubspot_id and db.prospect_existe_par_champ_perso("hubspot_id", hubspot_id, profil)
            )
            if deja_present:
                ignores.append(nom_affiche)
                continue
            try:
                db.add_prospect(donnees, champs_perso=champs_perso)
                ajoutes += 1
            except Exception:  # noqa: BLE001 - doublon linkedin_url le plus souvent
                ignores.append(nom_affiche)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"erreur": f"Import impossible : {exc}"}), 400
    finally:
        _nettoyer_import_en_attente(token)

    message = f"{ajoutes} prospect(s) importé(s) dans « {profil} »."
    if ignores:
        message += f" {len(ignores)} ignoré(s) (doublons) : {', '.join(ignores[:5])}{'...' if len(ignores) > 5 else ''}."
    return jsonify({"ok": True, "message": message, "ajoutes": ajoutes, "ignores": len(ignores)})


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
        noms_champs_deja_crees = {c["nom"] for c in profils.load_champs(profil)}
        for row in csv_module.DictReader(io.StringIO(contenu)):
            donnees, champs_perso = _mapper_ligne_csv(row, profil, noms_champs_deja_crees)
            donnees["profil"] = profil
            donnees.setdefault("source", "import_csv")
            nom_affiche = f"{donnees.get('prenom', '')} {donnees.get('nom', '')}".strip() or donnees.get("email") or "?"
            # Dédoublonnage par email au sein du profil : la contrainte UNIQUE
            # de la base ne porte que sur linkedin_url — un CSV sans colonne
            # LinkedIn ré-importé créait des doublons silencieux, chacun
            # recevant ensuite son propre brouillon (même personne emailée
            # plusieurs fois). Le doublon est compté et affiché comme les
            # autres, pas ignoré en silence. Une ligne sans email n'est
            # jamais un doublon d'une autre ligne sans email (impossible à
            # comparer) — chacune reste un prospect à part entière.
            # Dédoublonnage par email ET par hubspot_id : un contact sans
            # email (ça arrive — 11 sur 78 dans un vrai export HubSpot) ne
            # peut jamais matcher sur l'email seul, il se recréerait donc en
            # double à chaque réimport si on ne vérifiait que ça.
            hubspot_id = champs_perso.get("hubspot_id", "")
            deja_present = db.prospect_existe_par_email(donnees.get("email", ""), profil) or (
                hubspot_id and db.prospect_existe_par_champ_perso("hubspot_id", hubspot_id, profil)
            )
            if deja_present:
                ignores.append(nom_affiche)
                continue
            try:
                db.add_prospect(donnees, champs_perso=champs_perso)
                ajoutes += 1
            except Exception:  # noqa: BLE001 - doublon linkedin_url le plus souvent
                ignores.append(nom_affiche)
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
