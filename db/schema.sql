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
    getsales_lead_uuid  TEXT,
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

    score_qualification INTEGER,
    raison_qualification TEXT,
    signaux_positifs    TEXT,             -- JSON list (sqlite n'a pas de type array)
    signaux_negatifs    TEXT,

    -- Multi-profil : chaque prospect appartient à un profil (sammpo, medical...)
    -- et n'apparaît que quand ce profil est actif dans l'interface.
    profil              TEXT NOT NULL DEFAULT 'sammpo',

    -- Relances : incrémenté à chaque relance envoyée, comparé au max configuré.
    nb_relances         INTEGER NOT NULL DEFAULT 0,

    source              TEXT,
    date_creation        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    date_derniere_action TIMESTAMP,
    notes                TEXT
);

CREATE TABLE IF NOT EXISTS interactions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id  INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    type         TEXT NOT NULL,   -- email_envoye, relance_envoyee, email_recu, qualification, note, statut_manuel
    contenu      TEXT,
    date         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_prospects_statut ON prospects(statut);
CREATE INDEX IF NOT EXISTS idx_prospects_profil ON prospects(profil);
CREATE INDEX IF NOT EXISTS idx_interactions_prospect ON interactions(prospect_id);
CREATE INDEX IF NOT EXISTS idx_interactions_type_date ON interactions(type, date);

-- Évite de reclasser le même email à chaque exécution de l'agent email.
CREATE TABLE IF NOT EXISTS emails_traites (
    message_id      TEXT PRIMARY KEY,
    prospect_id     INTEGER REFERENCES prospects(id) ON DELETE CASCADE,
    date_traitement TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Brouillons en attente de validation. Persistés en base : un redémarrage
-- du serveur ne fait plus perdre les brouillons générés (et payés).
CREATE TABLE IF NOT EXISTS brouillons (
    prospect_id     INTEGER PRIMARY KEY REFERENCES prospects(id) ON DELETE CASCADE,
    objet           TEXT NOT NULL,
    corps           TEXT NOT NULL,
    type            TEXT NOT NULL DEFAULT 'initial'   -- initial | relance
                     CHECK (type IN ('initial', 'relance')),
    date_generation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Réglages clé/valeur de l'application (profil actif, limites, délais...).
CREATE TABLE IF NOT EXISTS reglages (
    cle    TEXT PRIMARY KEY,
    valeur TEXT
);
