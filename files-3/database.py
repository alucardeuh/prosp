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


def init_db(db_path: Path = DB_PATH) -> None:
    """Crée la base et les tables si elles n'existent pas encore."""
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.commit()


@contextmanager
def get_connection(db_path: Path = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def add_prospect(data: dict, db_path: Path = DB_PATH) -> int:
    """Insère un prospect. `data` peut contenir n'importe quel sous-ensemble
    des colonnes de la table prospects. Retourne l'id inséré."""
    champs_valides = {
        "prenom", "nom", "poste", "entreprise", "secteur",
        "taille_entreprise", "linkedin_url", "email", "source", "notes",
    }
    data = {k: v for k, v in data.items() if k in champs_valides}
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
        row = conn.execute(
            "SELECT * FROM prospects WHERE id = ?", (prospect_id,)
        ).fetchone()
        return dict(row) if row else None


def list_prospects(statut: str | None = None, db_path: Path = DB_PATH) -> list[dict]:
    with get_connection(db_path) as conn:
        if statut:
            rows = conn.execute(
                "SELECT * FROM prospects WHERE statut = ? ORDER BY date_creation",
                (statut,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM prospects ORDER BY date_creation"
            ).fetchall()
        return [dict(r) for r in rows]


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


def add_interaction(prospect_id: int, type_: str, contenu: str, db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO interactions (prospect_id, type, contenu) VALUES (?, ?, ?)",
            (prospect_id, type_, contenu),
        )


def list_prospects_avec_email(db_path: Path = DB_PATH) -> list[dict]:
    """Prospects qu'on peut chercher dans Gmail. On exclut les désinscrits :
    une fois qu'un prospect s'est désinscrit, on arrête de le regarder."""
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


def list_interactions(prospect_id: int, db_path: Path = DB_PATH) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM interactions WHERE prospect_id = ? ORDER BY date DESC",
            (prospect_id,),
        ).fetchall()
        return [dict(r) for r in rows]
