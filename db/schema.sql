-- Schéma de la base de prospection
-- Tous les agents lisent/écrivent ici plutôt que de se parler entre eux.

CREATE TABLE IF NOT EXISTS prospects (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    prenom              TEXT,
    nom                 TEXT,
    poste               TEXT,
    entreprise          TEXT,
    secteur             TEXT,
    taille_entreprise   TEXT,
    linkedin_url        TEXT UNIQUE,
    email               TEXT,

    -- Cycle de vie du prospect. C'est CE champ que le manager regarde
    -- pour décider quel agent doit agir ensuite.
    -- nouveau -> qualifie / disqualifie -> contacte -> repondu -> rdv / perdu
    -- desinscrit peut arriver à tout moment et doit tout bloquer.
    statut              TEXT NOT NULL DEFAULT 'nouveau'
                         CHECK (statut IN (
                             'nouveau', 'qualifie', 'disqualifie',
                             'contacte', 'repondu', 'rdv', 'perdu', 'desinscrit'
                         )),

    score_qualification INTEGER,          -- 0-100, rempli par l'agent qualification
    raison_qualification TEXT,
    signaux_positifs    TEXT,             -- JSON list, texte brut ici (sqlite n'a pas de type array)
    signaux_negatifs    TEXT,

    source              TEXT,             -- linkedin, import_csv, salon, referral...
    date_creation        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    date_derniere_action TIMESTAMP,
    notes                TEXT
);

CREATE TABLE IF NOT EXISTS interactions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id  INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    type         TEXT NOT NULL,   -- email_envoye, email_recu, linkedin_message, qualification, note
    contenu      TEXT,
    date         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_prospects_statut ON prospects(statut);
CREATE INDEX IF NOT EXISTS idx_interactions_prospect ON interactions(prospect_id);

-- Évite de reclasser le même email à chaque exécution de l'agent email.
CREATE TABLE IF NOT EXISTS emails_traites (
    message_id      TEXT PRIMARY KEY,
    prospect_id     INTEGER REFERENCES prospects(id) ON DELETE CASCADE,
    date_traitement TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
