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
    telephone           TEXT,
    getsales_lead_uuid  TEXT,
    email               TEXT,

    -- Cycle de vie du prospect. C'est CE champ que le manager regarde
    -- pour décider quel agent doit agir ensuite.
    -- nouveau -> qualifie / disqualifie -> contacte -> repondu -> rdv / perdu / rebond
    -- desinscrit peut arriver à tout moment et doit tout bloquer.
    statut              TEXT NOT NULL DEFAULT 'nouveau'
                         CHECK (statut IN (
                             'nouveau', 'qualifie', 'disqualifie',
                             'contacte', 'repondu', 'rdv', 'perdu', 'rebond', 'desinscrit'
                         )),

    score_qualification INTEGER,
    raison_qualification TEXT,
    signaux_positifs    TEXT,             -- JSON list (sqlite n'a pas de type array)
    signaux_negatifs    TEXT,

    -- Multi-profil : chaque prospect appartient à un profil (sammpo, medical...)
    -- et n'apparaît que quand ce profil est actif dans l'interface.
    profil              TEXT NOT NULL DEFAULT 'sammpo',

    -- Champs personnalisés (définis par profil dans config/profils/<profil>/champs.yaml,
    -- éditables depuis /ajouter) — stockés en JSON plutôt qu'en colonnes SQL
    -- réelles pour ne jamais avoir à modifier le schéma quand tu ajoutes ou
    -- retires une variable. {"nom_technique": "valeur", ...}
    champs_perso        TEXT NOT NULL DEFAULT '{}',

    -- Résultat mis en cache de la dernière vérification d'email (syntaxe +
    -- MX) : NULL = jamais vérifié, 'valide' / 'invalide' / 'inconnu'.
    -- Remis à NULL dès que l'email change (voir update_prospect), pour
    -- forcer une nouvelle vérification plutôt que de garder un résultat
    -- devenu obsolète.
    email_verifie        TEXT,

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

    -- Renseigné uniquement pour email_envoye / relance_envoyee : permet à la
    -- relance suivante de s'accrocher au même fil Gmail (threadId + en-tête
    -- Message-ID RFC pour In-Reply-To/References) plutôt que d'arriver comme
    -- un email tout neuf dans la boîte du prospect.
    gmail_thread_id TEXT,
    rfc_message_id  TEXT,

    -- Coût réel de l'appel API Claude qui a produit cette interaction
    -- (qualification, email_envoye, relance_envoyee, email_recu). 0 pour
    -- les interactions qui ne viennent pas d'un appel API (note, statut_manuel).
    tokens_entree   INTEGER NOT NULL DEFAULT 0,
    tokens_sortie   INTEGER NOT NULL DEFAULT 0,
    recherches_web  INTEGER NOT NULL DEFAULT 0,

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

    -- Cumulés à travers les régénérations : si tu régénères 3 fois avant
    -- d'envoyer, le coût réel inclut les 3 tentatives, pas juste la
    -- dernière — ces totaux sont recopiés dans l'interaction finale au
    -- moment de l'envoi (voir envoyer_brouillon).
    tokens_entree   INTEGER NOT NULL DEFAULT 0,
    tokens_sortie   INTEGER NOT NULL DEFAULT 0,
    recherches_web  INTEGER NOT NULL DEFAULT 0,

    -- Envoi programmé (facultatif) : NULL = envoi manuel classique (clic sur
    -- "Envoyer"). Une date/heure ISO locale ('YYYY-MM-DDTHH:MM') fait
    -- envoyer ce brouillon automatiquement dès que cette échéance est
    -- atteinte, tant que l'app tourne (voir dashboard/planificateur.py) —
    -- reste envoyable manuellement avant l'échéance si besoin.
    date_envoi_prevue TEXT,

    -- Mis de côté (bouton "Mettre de côté", ex-"Passer") : le brouillon
    -- n'est PAS supprimé, juste rangé dans un onglet séparé pour ne pas
    -- encombrer la liste active — récupérable ou supprimable pour de bon
    -- depuis cet onglet.
    mis_de_cote       INTEGER NOT NULL DEFAULT 0,

    date_generation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Réglages clé/valeur de l'application (profil actif, limites, délais...).
CREATE TABLE IF NOT EXISTS reglages (
    cle    TEXT PRIMARY KEY,
    valeur TEXT
);
