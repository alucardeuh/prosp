# Prosp — prospection B2B pilotée par Claude

Système de prospection commerciale : qualification des prospects par rapport
à ton ICP, emails rédigés par Claude un par un (avec recherche d'actualité
sur l'entreprise), lecture et classement automatique des réponses Gmail,
relances, stats — le tout piloté depuis une interface web locale, sans
Terminal. **Rien ne part jamais sans ton clic.**

## Démarrage

```bash
pip3 install -r requirements.txt
cp .env.example .env        # colle ta clé ANTHROPIC_API_KEY dedans
python3 -m dashboard.app
```

Puis ouvre **http://127.0.0.1:5001** (port 5001 : AirPlay squatte le 5000
sur macOS). L'interface ne tourne qu'en local (127.0.0.1), jamais exposée
sur le réseau.

## Les pages

- **Pipeline** (`/`) — barre de pipeline cliquable par statut, table triable
  (clic sur les en-têtes), recherche instantanée, changement de statut
  directement dans la liste. Boutons « Qualifier les nouveaux » et
  « Vérifier les réponses » : tout tourne en arrière-plan avec une barre de
  progression, la page ne gèle plus jamais.
- **Envoi** (`/envoi`) — génère les brouillons manquants **en masse d'un
  clic** (plafonné au quota du jour), puis revue rapide : chaque email est
  **éditable** (objet + corps) avant envoi. Envoyer / Sauver / Regénérer /
  Passer.
- **Relances** (`/relances`) — prospects contactés depuis plus de N jours
  sans réponse. Relances courtes (60-80 mots) qui font référence au premier
  email envoyé (stocké en base), angle nouveau, générées en masse ou une
  par une, toujours éditables et validées à la main.
- **Ajouter** (`/ajouter`) — un prospect à la main, ou import CSV en masse
  (doublons rejetés proprement).
- **Stats** (`/stats`) — taux de réponse, taux de qualification, RDV,
  désinscrits, graphe des envois par semaine (premiers emails vs relances).
- **Paramètres** (`/parametres`) — ICP, ton des emails, limites d'envoi,
  délai/nombre de relances, création de profils, test Gmail.
- **Fiche prospect** (`/prospect/N`) — tous les champs éditables, notes de
  suivi horodatées, historique complet, suppression.

## Multi-profil (SAMMPO aujourd'hui, ton projet médical demain)

Un **profil** = une offre + une cible : son ICP, son ton d'email, et ses
prospects. Tout vit dans `config/profils/<nom>/`. Le profil actif se change
dans la barre latérale ; chaque prospect appartient au profil actif au
moment de son ajout, et la qualification / rédaction utilisent toujours la
config du profil du moment. Crée le profil `medical` dans Paramètres le
jour venu : même code, offre et cible différentes, bases de prospects
séparées.

## Garde-fous (non négociables)

- **Validation humaine** : aucun email ne part sans clic sur « Envoyer ».
- **Quota quotidien** : 50 envois/jour par défaut (premiers emails +
  relances confondus), réglable, appliqué côté serveur.
- **Désinscription** : un prospect `desinscrit` est bloqué à l'envoi quoi
  qu'il arrive, et la lecture des réponses classe en désinscription au
  moindre doute (obligation RGPD/CPCE). La mention STOP est imposée dans
  chaque email généré.
- **Max relances** : 2 par prospect par défaut, réglable.

## Configuration Gmail (une seule fois)

1. [console.cloud.google.com](https://console.cloud.google.com) → crée un projet.
2. « APIs & Services » → « Library » → active **Gmail API**.
3. « OAuth consent screen » : type External, ajoute ton adresse en « Test users ».
4. « Credentials » → « Create Credentials » → « OAuth client ID » → **Desktop app**.
5. Télécharge le JSON → renomme-le `client_secret.json` → place-le dans `credentials/`.

Premier test : bouton « Tester la connexion Gmail » dans Paramètres (un
onglet s'ouvre pour l'autorisation ; l'écran « unsafe » est normal pour une
app perso non vérifiée — Advanced → Go to…).

## Ligne de commande (optionnelle)

L'interface couvre tout, mais les scripts restent utilisables :

```bash
python3 -m agents.qualification --dry-run     # teste sans clé API
python3 -m agents.email_reader --test-connexion
python3 -m manager --dry-run                  # cycle qualification + lecture
python3 -m scripts.cli list --statut qualifie
```

## Architecture

```
config/profils/<profil>/   icp.yaml + email_brief.yaml par profil
db/                        schema.sql + database.py (SQLite, tout l'état)
agents/                    qualification, email_sender (+ relances), email_reader
integrations/gmail_client  OAuth Gmail lecture + envoi
dashboard/                 Flask (app.py), jobs en arrière-plan (jobs.py),
                           templates + static (aucune dépendance front)
manager.py                 orchestrateur CLI (qualification + lecture)
```

Modèle Claude : `claude-sonnet-4-6` par défaut, surchargeable via
`CLAUDE_MODEL` dans `.env`. La recherche web des emails passe par l'outil
serveur Anthropic (même clé API, max 3 recherches par email).

## LinkedIn

Volontairement hors-projet : Octopus CRM directement (variables
prénom/nom/entreprise dans son interface). Une intégration API (Unipile)
reste possible si un jour la sélection des contacts doit être pilotée par
la qualification Claude.
