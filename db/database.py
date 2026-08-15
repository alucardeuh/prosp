"""
Wrapper SQLite pour la base de prospection.
Un seul point d'accès à la base -> chaque agent l'importe, personne ne
manipule sqlite3 directement ailleurs dans le projet.
"""
from __future__ import annotations  # requis pour `dict | None` etc. sous Python 3.9

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "prospection.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

REGLAGES_DEFAUT = {
    "profil_actif": "sammpo",
    "limite_envois_jour": "50",
    "delai_relance_jours": "7",
    "max_relances": "2",
    # Nombre de recherches web autorisées par email INITIAL (les relances
    # n'en font jamais, par design). 0 = recherche désactivée : Claude
    # rédige uniquement à partir du profil du prospect. La recherche coûte
    # $10/1000 recherches + le coût en tokens du contenu rapporté (parfois
    # plusieurs milliers de tokens) — donc réglable plutôt qu'imposé.
    "max_recherches_web": "3",
    # Thinking adaptatif pour la rédaction (Sonnet uniquement) — désactivé
    # par défaut. Voir le commentaire de _niveau_reflexion() dans
    # agents/email_sender.py pour le pourquoi (comportement par défaut de
    # l'API depuis Sonnet 5, coût invisible sinon).
    "niveau_reflexion": "desactive",
}


def init_db(db_path: Path = DB_PATH) -> None:
    """Crée la base et les tables si elles n'existent pas encore.
    Ajoute aussi les colonnes manquantes sur une base déjà existante
    (migration légère, sans framework dédié — le projet est encore petit).

    Ordre important : les colonnes manquantes sont ajoutées AVANT
    d'exécuter schema.sql, parce que celui-ci contient des CREATE INDEX
    sur ces colonnes — sur une base plus ancienne, l'index planterait
    si la colonne n'existait pas encore."""
    with sqlite3.connect(db_path) as conn:
        # Mode WAL : les lectures (navigation dans l'interface) ne sont plus
        # bloquées pendant qu'un job en arrière-plan écrit en base (relance,
        # brouillon, statut...). Sans ça, chaque écriture d'un job pouvait
        # faire ramer une page ouverte au même moment. synchronous=NORMAL est
        # le compagnon habituel du mode WAL : reste sûr contre un crash de
        # l'appli (ce qui nous intéresse), juste pas contre une coupure de
        # courant en pleine écriture — négligeable pour un usage perso.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
        if "prospects" in tables:
            colonnes = {row[1] for row in conn.execute("PRAGMA table_info(prospects)").fetchall()}
            if "getsales_lead_uuid" not in colonnes:
                conn.execute("ALTER TABLE prospects ADD COLUMN getsales_lead_uuid TEXT")
            if "profil" not in colonnes:
                conn.execute("ALTER TABLE prospects ADD COLUMN profil TEXT NOT NULL DEFAULT 'sammpo'")
            if "nb_relances" not in colonnes:
                conn.execute("ALTER TABLE prospects ADD COLUMN nb_relances INTEGER NOT NULL DEFAULT 0")
            if "champs_perso" not in colonnes:
                conn.execute("ALTER TABLE prospects ADD COLUMN champs_perso TEXT NOT NULL DEFAULT '{}'")
            if "email_verifie" not in colonnes:
                conn.execute("ALTER TABLE prospects ADD COLUMN email_verifie TEXT")
            if "telephone" not in colonnes:
                conn.execute("ALTER TABLE prospects ADD COLUMN telephone TEXT")
        if "interactions" in tables:
            cols_int = {row[1] for row in conn.execute("PRAGMA table_info(interactions)").fetchall()}
            if "gmail_thread_id" not in cols_int:
                conn.execute("ALTER TABLE interactions ADD COLUMN gmail_thread_id TEXT")
            if "rfc_message_id" not in cols_int:
                conn.execute("ALTER TABLE interactions ADD COLUMN rfc_message_id TEXT")
            for col in ("tokens_entree", "tokens_sortie", "recherches_web"):
                if col not in cols_int:
                    conn.execute(f"ALTER TABLE interactions ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
        if "brouillons" in tables:
            cols_br = {row[1] for row in conn.execute("PRAGMA table_info(brouillons)").fetchall()}
            for col in ("tokens_entree", "tokens_sortie", "recherches_web"):
                if col not in cols_br:
                    conn.execute(f"ALTER TABLE brouillons ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
            if "date_envoi_prevue" not in cols_br:
                conn.execute("ALTER TABLE brouillons ADD COLUMN date_envoi_prevue TEXT")
            if "mis_de_cote" not in cols_br:
                conn.execute("ALTER TABLE brouillons ADD COLUMN mis_de_cote INTEGER NOT NULL DEFAULT 0")
        conn.executescript(SCHEMA_PATH.read_text())
        for cle, valeur in REGLAGES_DEFAUT.items():
            conn.execute("INSERT OR IGNORE INTO reglages (cle, valeur) VALUES (?, ?)", (cle, valeur))
        conn.commit()


@contextmanager
def get_connection(db_path: Path = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- réglages

def get_reglage(cle: str, db_path: Path = DB_PATH) -> str:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT valeur FROM reglages WHERE cle = ?", (cle,)).fetchone()
        return row["valeur"] if row else REGLAGES_DEFAUT.get(cle, "")


def set_reglage(cle: str, valeur: str, db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO reglages (cle, valeur) VALUES (?, ?) "
            "ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
            (cle, str(valeur)),
        )


def profil_actif(db_path: Path = DB_PATH) -> str:
    return get_reglage("profil_actif", db_path) or "sammpo"


# ---------------------------------------------------------------- prospects

CHAMPS_PROSPECT = {
    "prenom", "nom", "poste", "entreprise", "secteur",
    "taille_entreprise", "linkedin_url", "telephone", "email", "source", "notes", "profil",
}

TRIS_AUTORISES = {
    "date_creation", "nom", "entreprise", "score_qualification",
    "statut", "date_derniere_action",
}


def add_prospect(data: dict, champs_perso: dict | None = None, db_path: Path = DB_PATH) -> int:
    """Insère un prospect. champs_perso est un dict de valeurs pour les
    variables personnalisées définies dans config/profils/<profil>/champs.yaml
    (voir profils.py) — stocké en JSON, indépendant des colonnes fixes.
    Si aucun profil n'est précisé (scripts CLI, import direct), rattache au
    profil actif plutôt qu'au défaut du schéma — pour que tout reste
    cohérent avec l'interface."""
    data = {k: v for k, v in data.items() if k in CHAMPS_PROSPECT}
    if not data.get("profil"):
        data["profil"] = profil_actif(db_path)
    data["champs_perso"] = json.dumps(champs_perso or {}, ensure_ascii=False)
    colonnes = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            f"INSERT INTO prospects ({colonnes}) VALUES ({placeholders})",
            list(data.values()),
        )
        return cur.lastrowid


def update_champs_perso(prospect_id: int, valeurs: dict, db_path: Path = DB_PATH) -> None:
    """Fusionne (ne remplace pas) les valeurs de champs personnalisés d'un
    prospect — modifier un seul champ ne doit jamais effacer les autres."""
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT champs_perso FROM prospects WHERE id = ?", (prospect_id,)).fetchone()
        actuel = json.loads(row["champs_perso"]) if row and row["champs_perso"] else {}
        actuel.update(valeurs)
        conn.execute(
            "UPDATE prospects SET champs_perso = ?, date_derniere_action = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(actuel, ensure_ascii=False), prospect_id),
        )


def get_prospect(prospect_id: int, db_path: Path = DB_PATH) -> dict | None:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM prospects WHERE id = ?", (prospect_id,)).fetchone()
        return dict(row) if row else None


def get_prospects(ids: list[int], db_path: Path = DB_PATH) -> list[dict]:
    """Récupère plusieurs prospects par leurs ids en une seule requête,
    plutôt qu'un get_prospect() par id dans une boucle — utile pour les
    sélections sur mesure (qualification, génération) où l'appelant a déjà
    une liste d'ids issue de cases cochées."""
    if not ids:
        return []
    with get_connection(db_path) as conn:
        marqueurs = ",".join("?" * len(ids))
        rows = conn.execute(f"SELECT * FROM prospects WHERE id IN ({marqueurs})", list(ids)).fetchall()
        return [dict(r) for r in rows]


def update_prospect(prospect_id: int, data: dict, db_path: Path = DB_PATH) -> None:
    """Met à jour les champs éditables d'un prospect (fiche)."""
    data = {k: v for k, v in data.items() if k in CHAMPS_PROSPECT}
    if not data:
        return
    with get_connection(db_path) as conn:
        # L'email change -> le dernier résultat de vérification n'a plus de
        # sens (c'était pour une AUTRE adresse) : on le remet à zéro plutôt
        # que de laisser un statut "valide"/"invalide" trompeur.
        if "email" in data:
            actuel = conn.execute("SELECT email FROM prospects WHERE id = ?", (prospect_id,)).fetchone()
            if not actuel or actuel["email"] != data["email"]:
                data = dict(data)
                data["email_verifie"] = None
        assignations = ", ".join(f"{k} = ?" for k in data)
        conn.execute(
            f"UPDATE prospects SET {assignations}, date_derniere_action = CURRENT_TIMESTAMP WHERE id = ?",
            list(data.values()) + [prospect_id],
        )


def delete_prospect(prospect_id: int, db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM prospects WHERE id = ?", (prospect_id,))


def delete_prospects(ids: list[int], profil: str, db_path: Path = DB_PATH) -> int:
    """Supprime plusieurs prospects d'un coup, scopé au profil (un id d'un
    autre profil dans la liste est ignoré plutôt que de risquer une
    suppression croisée). Retourne le nombre réellement supprimé."""
    if not ids:
        return 0
    with get_connection(db_path) as conn:
        marqueurs = ",".join("?" * len(ids))
        curseur = conn.execute(
            f"DELETE FROM prospects WHERE profil = ? AND id IN ({marqueurs})",
            [profil] + list(ids),
        )
        return curseur.rowcount


def delete_tous_prospects(profil: str, db_path: Path = DB_PATH) -> int:
    """Supprime TOUS les prospects d'un profil — irréversible. Retourne le
    nombre supprimé."""
    with get_connection(db_path) as conn:
        curseur = conn.execute("DELETE FROM prospects WHERE profil = ?", (profil,))
        return curseur.rowcount


def list_prospects_pour_selection(profil: str, db_path: Path = DB_PATH) -> list[dict]:
    """Tous les prospects du profil AVEC UN EMAIL, désinscrits exclus (jamais
    sélectionnables pour un envoi, quoi qu'il arrive), avec leur nombre
    d'envois déjà réalisés (email_envoye + relance_envoyee confondus) —
    sert à la table de sélection sur mesure de /envoi, indépendamment du
    statut de qualification. Sans email, un prospect n'est techniquement
    pas contactable par cette voie : il n'a rien à faire dans une table
    dont le seul but est de choisir qui recevra un email (il reste bien
    sûr visible partout ailleurs — Pipeline, sa fiche — juste pas ici)."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT p.*, COALESCE(e.nb_envois, 0) AS nb_envois
               FROM prospects p
               LEFT JOIN (
                   SELECT prospect_id, COUNT(*) AS nb_envois FROM interactions
                   WHERE type IN ('email_envoye', 'relance_envoyee')
                   GROUP BY prospect_id
               ) e ON e.prospect_id = p.id
               WHERE p.profil = ? AND p.statut != 'desinscrit'
                 AND p.email IS NOT NULL AND p.email != ''
               ORDER BY p.date_creation DESC""",
            (profil,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_prospects(
    statut: str | None = None,
    profil: str | None = None,
    recherche: str | None = None,
    tri: str = "date_creation",
    ordre: str = "desc",
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Liste filtrée / cherchée / triée. Tous les paramètres sont optionnels."""
    clauses, params = [], []
    if statut:
        clauses.append("statut = ?")
        params.append(statut)
    if profil:
        clauses.append("profil = ?")
        params.append(profil)
    if recherche:
        clauses.append("(prenom LIKE ? OR nom LIKE ? OR entreprise LIKE ? OR poste LIKE ? OR email LIKE ?)")
        motif = f"%{recherche}%"
        params.extend([motif] * 5)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    tri = tri if tri in TRIS_AUTORISES else "date_creation"
    ordre = "ASC" if str(ordre).lower() == "asc" else "DESC"
    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM prospects {where} ORDER BY {tri} {ordre} NULLS LAST",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def counts_par_statut(profil: str | None = None, db_path: Path = DB_PATH) -> dict:
    with get_connection(db_path) as conn:
        if profil:
            rows = conn.execute(
                "SELECT statut, COUNT(*) AS n FROM prospects WHERE profil = ? GROUP BY statut",
                (profil,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT statut, COUNT(*) AS n FROM prospects GROUP BY statut").fetchall()
        return {r["statut"]: r["n"] for r in rows}


def count_qualifies_avec_email(profil: str, db_path: Path = DB_PATH) -> int:
    """Nombre de prospects qualifiés avec un email, prêts pour /envoi. Un
    COUNT SQL direct plutôt que charger toute la liste juste pour la compter
    — utilisé pour le badge de la barre latérale, affiché sur CHAQUE page."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM prospects "
            "WHERE statut = 'qualifie' AND profil = ? AND email IS NOT NULL AND email != ''",
            (profil,),
        ).fetchone()
        return row["n"]


def update_qualification(
    prospect_id: int,
    qualifie: bool,
    score: int,
    raison: str,
    signaux_positifs: list[str],
    signaux_negatifs: list[str],
    tokens_entree: int = 0,
    tokens_sortie: int = 0,
    db_path: Path = DB_PATH,
) -> None:
    nouveau_statut = "qualifie" if qualifie else "disqualifie"
    with get_connection(db_path) as conn:
        conn.execute(
            """UPDATE prospects
               SET statut = ?, score_qualification = ?, raison_qualification = ?,
                   signaux_positifs = ?, signaux_negatifs = ?,
                   date_derniere_action = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (
                nouveau_statut,
                score,
                raison,
                json.dumps(signaux_positifs, ensure_ascii=False),
                json.dumps(signaux_negatifs, ensure_ascii=False),
                prospect_id,
            ),
        )
        conn.execute(
            "INSERT INTO interactions (prospect_id, type, contenu, tokens_entree, tokens_sortie) "
            "VALUES (?, 'qualification', ?, ?, ?)",
            (prospect_id, raison, tokens_entree, tokens_sortie),
        )


def update_statut(prospect_id: int, statut: str, db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """UPDATE prospects SET statut = ?, date_derniere_action = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (statut, prospect_id),
        )


def incrementer_relances(prospect_id: int, db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE prospects SET nb_relances = nb_relances + 1, "
            "date_derniere_action = CURRENT_TIMESTAMP WHERE id = ?",
            (prospect_id,),
        )


def add_interaction(prospect_id: int, type_: str, contenu: str,
                    gmail_thread_id: str | None = None, rfc_message_id: str | None = None,
                    tokens_entree: int = 0, tokens_sortie: int = 0, recherches_web: int = 0,
                    db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO interactions (prospect_id, type, contenu, gmail_thread_id, rfc_message_id, "
            "tokens_entree, tokens_sortie, recherches_web) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (prospect_id, type_, contenu, gmail_thread_id, rfc_message_id,
             tokens_entree, tokens_sortie, recherches_web),
        )


def list_interactions(prospect_id: int, db_path: Path = DB_PATH) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM interactions WHERE prospect_id = ? ORDER BY date DESC",
            (prospect_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def derniere_interaction(prospect_id: int, type_: str, db_path: Path = DB_PATH) -> dict | None:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM interactions WHERE prospect_id = ? AND type = ? ORDER BY date DESC LIMIT 1",
            (prospect_id, type_),
        ).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------- emails

def list_prospects_avec_email(profil: str, db_path: Path = DB_PATH) -> list[dict]:
    """Prospects de CE profil qu'on peut chercher dans Gmail — chaque profil
    a sa propre boîte Gmail connectée, donc plus de raison de regarder tous
    profils confondus comme avant.
    On exclut les désinscrits : une fois désinscrit, on arrête de regarder.
    On exclut aussi ceux qui n'ont jamais reçu d'email (statut 'nouveau',
    'disqualifie') : ils ne peuvent techniquement pas avoir répondu à un
    email jamais envoyé, ça ne sert qu'à gaspiller des requêtes Gmail
    (et son quota journalier) sans jamais rien trouver pour eux. On se base
    sur l'historique réel (EXISTS sur interactions) plutôt que sur le statut
    courant, qui peut avoir été changé à la main entre-temps."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT * FROM prospects
               WHERE profil = ?
                 AND email IS NOT NULL AND email != ''
                 AND statut != 'desinscrit'
                 AND EXISTS (
                     SELECT 1 FROM interactions i
                     WHERE i.prospect_id = prospects.id
                       AND i.type IN ('email_envoye', 'relance_envoyee')
                 )""",
            (profil,),
        ).fetchall()
        return [dict(r) for r in rows]


def est_email_traite(message_id: str, db_path: Path = DB_PATH) -> bool:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM emails_traites WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None


def marquer_email_traite(message_id: str, prospect_id: int | None, db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO emails_traites (message_id, prospect_id) VALUES (?, ?)",
            (message_id, prospect_id),
        )


# ---------------------------------------------------------------- brouillons

def set_brouillon(prospect_id: int, objet: str, corps: str, type_: str = "initial",
                  tokens_entree: int = 0, tokens_sortie: int = 0, recherches_web: int = 0,
                  db_path: Path = DB_PATH) -> None:
    """Crée ou remplace le brouillon d'un prospect. Le TEXTE est remplacé
    (une régénération écrase l'ancien objet/corps), mais les TOKENS
    s'accumulent : régénérer 3 fois avant d'envoyer doit refléter le coût
    réel des 3 tentatives, pas seulement de la dernière."""
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO brouillons (prospect_id, objet, corps, type, tokens_entree, tokens_sortie, recherches_web) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(prospect_id) DO UPDATE SET objet = excluded.objet, "
            "corps = excluded.corps, type = excluded.type, date_generation = CURRENT_TIMESTAMP, "
            "tokens_entree = brouillons.tokens_entree + excluded.tokens_entree, "
            "tokens_sortie = brouillons.tokens_sortie + excluded.tokens_sortie, "
            "recherches_web = brouillons.recherches_web + excluded.recherches_web",
            (prospect_id, objet, corps, type_, tokens_entree, tokens_sortie, recherches_web),
        )


def get_brouillon(prospect_id: int, db_path: Path = DB_PATH) -> dict | None:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM brouillons WHERE prospect_id = ?", (prospect_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_brouillon(prospect_id: int, db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM brouillons WHERE prospect_id = ?", (prospect_id,))


def mettre_brouillon_de_cote(prospect_id: int, db_path: Path = DB_PATH) -> None:
    """'Passer' un brouillon ne le supprime plus — il est juste rangé dans
    un onglet séparé, pour ne jamais perdre un texte déjà écrit (à la main
    ou par l'IA, donc parfois des tokens déjà dépensés)."""
    with get_connection(db_path) as conn:
        conn.execute("UPDATE brouillons SET mis_de_cote = 1 WHERE prospect_id = ?", (prospect_id,))


def reprendre_brouillon(prospect_id: int, db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute("UPDATE brouillons SET mis_de_cote = 0 WHERE prospect_id = ?", (prospect_id,))


def list_brouillons(db_path: Path = DB_PATH) -> dict:
    """Tous les brouillons, indexés par prospect_id."""
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM brouillons").fetchall()
        return {r["prospect_id"]: dict(r) for r in rows}


def list_prospects_avec_brouillon(profil: str, db_path: Path = DB_PATH) -> list[dict]:
    """Prospects du profil qui ont un brouillon (avec email), chacun avec
    son brouillon déjà attaché sous p['brouillon'] — une seule requête
    jointe, plutôt que de récupérer TOUS les brouillons tous profils
    confondus puis faire un get_prospect() individuel par brouillon avant
    de filtrer (l'ancienne approche de /envoi : 1 + N requêtes au lieu
    d'une seule, la plupart jetées après coup car d'un autre profil)."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT p.*,
                      b.objet AS b_objet, b.corps AS b_corps, b.type AS b_type,
                      b.tokens_entree AS b_tokens_entree, b.tokens_sortie AS b_tokens_sortie,
                      b.recherches_web AS b_recherches_web,
                      b.date_envoi_prevue AS b_date_envoi_prevue, b.mis_de_cote AS b_mis_de_cote
               FROM prospects p
               JOIN brouillons b ON b.prospect_id = p.id
               WHERE p.profil = ? AND p.email IS NOT NULL AND p.email != ''""",
            (profil,),
        ).fetchall()
        resultats = []
        for r in rows:
            d = dict(r)
            d["brouillon"] = {
                "prospect_id": d["id"],
                "objet": d.pop("b_objet"),
                "corps": d.pop("b_corps"),
                "type": d.pop("b_type"),
                "tokens_entree": d.pop("b_tokens_entree"),
                "tokens_sortie": d.pop("b_tokens_sortie"),
                "recherches_web": d.pop("b_recherches_web"),
                "date_envoi_prevue": d.pop("b_date_envoi_prevue"),
                "mis_de_cote": d.pop("b_mis_de_cote"),
            }
            resultats.append(d)
        return resultats


# ---------------------------------------------------------------- quota & relances

def envois_du_jour(db_path: Path = DB_PATH) -> int:
    """Nombre d'emails (initiaux + relances) envoyés aujourd'hui, tous profils
    confondus — la limite protège la délivrabilité de la boîte Gmail, qui est
    unique, donc elle se compte globalement."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM interactions "
            "WHERE type IN ('email_envoye', 'relance_envoyee') "
            "AND date(date) = date('now', 'localtime')"
        ).fetchone()
        return row["n"]


def prospects_a_relancer(profil: str, delai_jours: int, max_relances: int,
                         db_path: Path = DB_PATH) -> list[dict]:
    """Prospects 'contacte' dont le dernier envoi date de plus de N jours,
    sans réponse entre temps, et qui n'ont pas dépassé le max de relances."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT p.*, MAX(i.date) AS date_dernier_envoi
               FROM prospects p
               JOIN interactions i ON i.prospect_id = p.id
                    AND i.type IN ('email_envoye', 'relance_envoyee')
               WHERE p.statut = 'contacte'
                 AND p.profil = ?
                 AND p.nb_relances < ?
               GROUP BY p.id
               HAVING julianday('now', 'localtime') - julianday(MAX(i.date)) >= ?
               ORDER BY date_dernier_envoi ASC""",
            (profil, max_relances, delai_jours),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------- stats

def stats_envois_par_semaine(nb_semaines: int = 8, db_path: Path = DB_PATH) -> list[dict]:
    """[{semaine: '2026-32', envois: n, relances: n}] sur les N dernières semaines."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT strftime('%Y-%W', date) AS semaine,
                      SUM(CASE WHEN type = 'email_envoye' THEN 1 ELSE 0 END) AS envois,
                      SUM(CASE WHEN type = 'relance_envoyee' THEN 1 ELSE 0 END) AS relances
               FROM interactions
               WHERE type IN ('email_envoye', 'relance_envoyee')
                 AND date >= date('now', 'localtime', ?)
               GROUP BY semaine ORDER BY semaine""",
            (f"-{nb_semaines * 7} days",),
        ).fetchall()
        return [dict(r) for r in rows]


def stats_globales(profil: str, db_path: Path = DB_PATH) -> dict:
    """Chiffres clés du profil : contactés, réponses, taux, désinscriptions..."""
    counts = counts_par_statut(profil=profil, db_path=db_path)
    contactes_cumules = sum(counts.get(s, 0) for s in ("contacte", "repondu", "rdv", "perdu", "desinscrit"))
    reponses = sum(counts.get(s, 0) for s in ("repondu", "rdv"))
    qualifies_cumules = contactes_cumules + counts.get("qualifie", 0)
    evalues = qualifies_cumules + counts.get("disqualifie", 0)
    return {
        "total": sum(counts.values()),
        "counts": counts,
        "contactes_cumules": contactes_cumules,
        "reponses": reponses,
        "taux_reponse": round(100 * reponses / contactes_cumules) if contactes_cumules else None,
        "taux_qualification": round(100 * qualifies_cumules / evalues) if evalues else None,
        "rdv": counts.get("rdv", 0),
        "desinscrits": counts.get("desinscrit", 0),
    }


def stats_tokens(profil: str, db_path: Path = DB_PATH) -> dict:
    """Tokens et recherches web consommés par le profil, tous prospects
    confondus, avec le détail par type d'interaction (qualification,
    email_envoye, relance_envoyee, email_recu) — pour la page Stats."""
    with get_connection(db_path) as conn:
        total = conn.execute(
            """SELECT COALESCE(SUM(i.tokens_entree), 0) AS tokens_entree,
                      COALESCE(SUM(i.tokens_sortie), 0) AS tokens_sortie,
                      COALESCE(SUM(i.recherches_web), 0) AS recherches_web
               FROM interactions i JOIN prospects p ON p.id = i.prospect_id
               WHERE p.profil = ?""",
            (profil,),
        ).fetchone()
        par_type = conn.execute(
            """SELECT i.type,
                      SUM(i.tokens_entree) AS tokens_entree,
                      SUM(i.tokens_sortie) AS tokens_sortie,
                      SUM(i.recherches_web) AS recherches_web
               FROM interactions i JOIN prospects p ON p.id = i.prospect_id
               WHERE p.profil = ? AND (i.tokens_entree > 0 OR i.tokens_sortie > 0)
               GROUP BY i.type""",
            (profil,),
        ).fetchall()
        return {
            "tokens_entree": total["tokens_entree"],
            "tokens_sortie": total["tokens_sortie"],
            "recherches_web": total["recherches_web"],
            "cout_recherches_usd": round(total["recherches_web"] * 10 / 1000, 3),
            "par_type": {r["type"]: dict(r) for r in par_type},
        }


def reparer_collision_champ_perso(nom_champ: str, profil: str, db_path: Path = DB_PATH) -> int:
    """Migration ponctuelle : une version antérieure permettait de créer un
    champ personnalisé portant le même nom qu'un champ fixe (ex : 'poste'),
    ce qui faisait dérouter silencieusement les valeurs saisies dans
    champs_perso au lieu de la vraie colonne. Recopie la valeur vers la
    vraie colonne si elle est vide, retire la clé du JSON dans tous les cas.
    Retourne le nombre de prospects où une valeur a réellement été récupérée.

    nom_champ DOIT être un nom de colonne réelle (vérifié contre
    CHAMPS_PROSPECT) avant d'être interpolé dans le SQL — même garde-fou que
    update_prospect, qui fait déjà ce type d'interpolation contrôlée."""
    if nom_champ not in CHAMPS_PROSPECT:
        return 0
    corriges = 0
    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT id, champs_perso, {nom_champ} AS valeur_actuelle FROM prospects WHERE profil = ?",
            (profil,),
        ).fetchall()
        for row in rows:
            valeurs = json.loads(row["champs_perso"] or "{}")
            if nom_champ not in valeurs:
                continue
            valeur_perso = valeurs.pop(nom_champ)
            nouveau_json = json.dumps(valeurs, ensure_ascii=False)
            if not row["valeur_actuelle"] and valeur_perso:
                conn.execute(
                    f"UPDATE prospects SET {nom_champ} = ?, champs_perso = ? WHERE id = ?",
                    (valeur_perso, nouveau_json, row["id"]),
                )
                corriges += 1
            else:
                conn.execute(
                    "UPDATE prospects SET champs_perso = ? WHERE id = ?",
                    (nouveau_json, row["id"]),
                )
    return corriges


def prospect_existe_par_email(email: str, profil: str, db_path: Path = DB_PATH) -> bool:
    """Vérifie si un prospect du profil a déjà cette adresse email (comparaison
    insensible à la casse et aux espaces) — sert au dédoublonnage à l'import
    CSV : la contrainte UNIQUE ne porte que sur linkedin_url, donc un CSV
    sans colonne LinkedIn ré-importé créait des doublons silencieux (même
    personne emailée plusieurs fois dans un même lot)."""
    email = (email or "").strip().lower()
    if not email:
        return False
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM prospects WHERE profil = ? "
            "AND LOWER(TRIM(email)) = ? LIMIT 1",
            (profil, email),
        ).fetchone()
        return row is not None


def prospect_existe_par_champ_perso(cle: str, valeur: str, profil: str, db_path: Path = DB_PATH) -> bool:
    """Vérifie si un prospect du profil a déjà cette valeur pour ce champ
    personnalisé — sert à éviter les doublons à l'import HubSpot, qui n'a
    pas toujours d'URL LinkedIn (donc pas de contrainte UNIQUE native
    comme pour l'import CSV)."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM prospects WHERE profil = ? "
            "AND json_extract(champs_perso, '$.' || ?) = ? LIMIT 1",
            (profil, cle, valeur),
        ).fetchone()
        return row is not None


def set_email_verifie(prospect_id: int, statut: str, db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute("UPDATE prospects SET email_verifie = ? WHERE id = ?", (statut, prospect_id))


def programmer_brouillon(prospect_id: int, date_envoi: str | None, db_path: Path = DB_PATH) -> None:
    """date_envoi : 'YYYY-MM-DDTHH:MM' (heure locale de la machine qui fait
    tourner l'app), ou None pour annuler une programmation existante."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE brouillons SET date_envoi_prevue = ? WHERE prospect_id = ?",
            (date_envoi, prospect_id),
        )


def list_brouillons_programmes_dus(db_path: Path = DB_PATH) -> list[dict]:
    """Brouillons dont l'échéance programmée est atteinte — vérifié par le
    planificateur en arrière-plan (dashboard/planificateur.py)."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM brouillons WHERE date_envoi_prevue IS NOT NULL "
            "AND datetime(date_envoi_prevue) <= datetime('now', 'localtime')"
        ).fetchall()
        return [dict(r) for r in rows]


def set_getsales_lead_uuid(prospect_id: int, lead_uuid: str, db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE prospects SET getsales_lead_uuid = ? WHERE id = ?",
            (lead_uuid, prospect_id),
        )
