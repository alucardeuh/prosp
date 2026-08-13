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
}


def init_db(db_path: Path = DB_PATH) -> None:
    """Crée la base et les tables si elles n'existent pas encore.
    Ajoute aussi les colonnes manquantes sur une base déjà existante
    (migration légère, sans framework dédié — le projet est encore petit)."""
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_PATH.read_text())
        colonnes = {row[1] for row in conn.execute("PRAGMA table_info(prospects)").fetchall()}
        if "getsales_lead_uuid" not in colonnes:
            conn.execute("ALTER TABLE prospects ADD COLUMN getsales_lead_uuid TEXT")
        if "profil" not in colonnes:
            conn.execute("ALTER TABLE prospects ADD COLUMN profil TEXT NOT NULL DEFAULT 'sammpo'")
        if "nb_relances" not in colonnes:
            conn.execute("ALTER TABLE prospects ADD COLUMN nb_relances INTEGER NOT NULL DEFAULT 0")
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
    "taille_entreprise", "linkedin_url", "email", "source", "notes", "profil",
}

TRIS_AUTORISES = {
    "date_creation", "nom", "entreprise", "score_qualification",
    "statut", "date_derniere_action",
}


def add_prospect(data: dict, db_path: Path = DB_PATH) -> int:
    """Insère un prospect. Retourne l'id inséré. Si aucun profil n'est
    précisé (scripts CLI, import direct), rattache au profil actif plutôt
    qu'au défaut du schéma — pour que tout reste cohérent avec l'interface."""
    data = {k: v for k, v in data.items() if k in CHAMPS_PROSPECT}
    if not data.get("profil"):
        data["profil"] = profil_actif(db_path)
    colonnes = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            f"INSERT INTO prospects ({colonnes}) VALUES ({placeholders})",
            list(data.values()),
        )
        return cur.lastrowid


def get_prospect(prospect_id: int, db_path: Path = DB_PATH) -> dict | None:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM prospects WHERE id = ?", (prospect_id,)).fetchone()
        return dict(row) if row else None


def update_prospect(prospect_id: int, data: dict, db_path: Path = DB_PATH) -> None:
    """Met à jour les champs éditables d'un prospect (fiche)."""
    data = {k: v for k, v in data.items() if k in CHAMPS_PROSPECT}
    if not data:
        return
    assignations = ", ".join(f"{k} = ?" for k in data)
    with get_connection(db_path) as conn:
        conn.execute(
            f"UPDATE prospects SET {assignations}, date_derniere_action = CURRENT_TIMESTAMP WHERE id = ?",
            list(data.values()) + [prospect_id],
        )


def delete_prospect(prospect_id: int, db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM prospects WHERE id = ?", (prospect_id,))


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


def update_qualification(
    prospect_id: int,
    qualifie: bool,
    score: int,
    raison: str,
    signaux_positifs: list[str],
    signaux_negatifs: list[str],
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
            "INSERT INTO interactions (prospect_id, type, contenu) VALUES (?, 'qualification', ?)",
            (prospect_id, raison),
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


def add_interaction(prospect_id: int, type_: str, contenu: str, db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO interactions (prospect_id, type, contenu) VALUES (?, ?, ?)",
            (prospect_id, type_, contenu),
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

def list_prospects_avec_email(db_path: Path = DB_PATH) -> list[dict]:
    """Prospects qu'on peut chercher dans Gmail (tous profils confondus :
    une réponse peut arriver même quand on travaille sur l'autre profil).
    On exclut les désinscrits : une fois désinscrit, on arrête de regarder."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM prospects WHERE email IS NOT NULL AND email != '' "
            "AND statut != 'desinscrit'"
        ).fetchall()
        return [dict(r) for r in rows]


def est_email_traite(message_id: str, db_path: Path = DB_PATH) -> bool:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM emails_traites WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None


def marquer_email_traite(message_id: str, prospect_id: int, db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO emails_traites (message_id, prospect_id) VALUES (?, ?)",
            (message_id, prospect_id),
        )


# ---------------------------------------------------------------- brouillons

def set_brouillon(prospect_id: int, objet: str, corps: str, type_: str = "initial",
                  db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO brouillons (prospect_id, objet, corps, type) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(prospect_id) DO UPDATE SET objet = excluded.objet, "
            "corps = excluded.corps, type = excluded.type, date_generation = CURRENT_TIMESTAMP",
            (prospect_id, objet, corps, type_),
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


def list_brouillons(db_path: Path = DB_PATH) -> dict:
    """Tous les brouillons, indexés par prospect_id."""
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM brouillons").fetchall()
        return {r["prospect_id"]: dict(r) for r in rows}


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


def set_getsales_lead_uuid(prospect_id: int, lead_uuid: str, db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE prospects SET getsales_lead_uuid = ? WHERE id = ?",
            (lead_uuid, prospect_id),
        )
