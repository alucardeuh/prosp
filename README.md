# Agents de prospection — Étape 1 : fondations + agent de qualification

Ce dossier contient le socle du projet décrit dans notre échange précédent :
une base de données partagée qui sert d'état pour tous les futurs agents,
et le premier agent (qualification), celui qui ne présente aucun risque
externe (pas de LinkedIn, pas d'email) et qui pose les bases du reste.

## Ce qu'il y a dedans

```
prospection-agents/
├── config/icp.yaml          <- À REMPLIR EN PREMIER (voir plus bas)
├── db/
│   ├── schema.sql            schéma de la base
│   └── database.py           toutes les fonctions CRUD (add_prospect, list_prospects, ...)
├── agents/
│   └── qualification.py      l'agent qui score chaque prospect par rapport à l'ICP
├── scripts/
│   ├── cli.py                ajouter/lister/voir un prospect à la main
│   └── import_csv.py         importer un export Clay/Apollo/PhantomBuster en masse
├── requirements.txt
└── .env.example
```

## Installation

```bash
cd prospection-agents
pip3 install -r requirements.txt
cp .env.example .env
# édite .env et colle ta clé API Anthropic (console.anthropic.com)
```

## Étape 1 — remplis ton ICP

Ouvre `config/icp.yaml` et remplace les placeholders par ton vrai produit et
ta vraie cible. **C'est le fichier le plus important du projet** : l'agent
de qualification ne fait que comparer chaque prospect à ce que tu as écrit
là. Un ICP vague = des scores qui ne veulent rien dire.

## Étape 2 — ajoute des prospects

À la main, pour tester :
```bash
python3 -m scripts.cli add --prenom Jean --nom Dupont --poste "Head of Sales" \
    --entreprise Acme --secteur "SaaS B2B" --taille "50-200" \
    --linkedin "https://linkedin.com/in/..." --email jean@acme.com
```

Ou en masse depuis un CSV (export Clay, Apollo, PhantomBuster...) :
```bash
python3 -m scripts.import_csv mon_export.csv
```

## Étape 3 — lance la qualification

Test sans consommer d'API (vérifie juste que la mécanique tourne) :
```bash
python3 -m agents.qualification --dry-run
```

Qualification réelle (nécessite `ANTHROPIC_API_KEY` dans `.env`) :
```bash
python3 -m agents.qualification
```

Un seul prospect :
```bash
python3 -m agents.qualification --prospect-id 3
```

## Vérifier le résultat

```bash
python3 -m scripts.cli list --statut qualifie
python3 -m scripts.cli show 1
```

Chaque qualification écrit aussi une ligne dans la table `interactions`,
donc tu as un historique de pourquoi chaque décision a été prise.

## Ce que ce socle a été testé pour couvrir

- Création de la base et des tables au premier lancement (`db.init_db()`)
- Ajout, listing filtré par statut, et détail d'un prospect
- Import CSV en masse (colonnes manquantes tolérées, doublons `linkedin_url` rejetés proprement)
- Le flux complet qualification -> écriture DB -> interaction loggée (testé en `--dry-run`)

Ce qui n'est PAS encore testé : un vrai appel à l'API Claude (nécessite ta
clé). La logique du prompt et du tool JSON est en place et suit le pattern
recommandé (sortie structurée via tool_choice forcé), mais vérifie les
premiers résultats à la main avant de faire tourner ça sur toute ta base.

## Agent email (lecture seule)

`agents/email_reader.py` cherche dans Gmail les réponses des prospects déjà
en base, les fait classer par Claude (intéressé / pas intéressé / à
relancer / désinscription / réponse automatique / autre), et met à jour
leur statut. **Il ne peut techniquement pas envoyer ni modifier quoi que
ce soit** : le scope OAuth utilisé (`gmail.readonly`) ne le permet pas côté
Google, même si le code essayait.

### Configuration Gmail (à faire une seule fois)

1. Va sur [console.cloud.google.com](https://console.cloud.google.com), crée un projet (ou réutilise un projet existant).
2. Dans "APIs & Services" > "Library", active **Gmail API**.
3. Dans "APIs & Services" > "OAuth consent screen" : type **External**, remplis les champs obligatoires (nom de l'app, email), et dans la section "Test users" ajoute ta propre adresse Gmail. Pas besoin de faire vérifier l'app par Google pour un usage perso.
4. Dans "APIs & Services" > "Credentials" > "Create Credentials" > "OAuth client ID", type d'application **Desktop app**.
5. Télécharge le JSON, renomme-le `client_secret.json`, place-le dans `credentials/`.

### Premier lancement

```bash
python3 -m agents.email_reader --test-connexion
```

Un navigateur s'ouvre pour l'autorisation. Comme l'app n'est pas vérifiée
par Google (normal pour un usage perso), tu verras un écran d'avertissement
— clique sur "Advanced" / "Paramètres avancés" puis "Go to [nom de l'app]
(unsafe)". C'est ton app à toi, sur ton compte à toi, donc pas de risque
réel. Le token est ensuite sauvegardé dans `credentials/token.json` et
réutilisé automatiquement (rafraîchi tout seul quand il expire).

### Usage

```bash
python3 -m agents.email_reader --dry-run          # teste le flux sans toucher Gmail ni l'API Claude
python3 -m agents.email_reader --test-connexion    # vérifie juste que l'OAuth fonctionne
python3 -m agents.email_reader                     # scan réel, nécessite ANTHROPIC_API_KEY
```

Chaque email classé écrit une ligne dans `interactions`, et le statut du
prospect est mis à jour automatiquement pour les cas clairs (`interesse`,
`pas_interesse`, `desinscription`). Pour `a_relancer` / `absence_bureau` /
`autre`, le statut n'est volontairement pas touché — c'est à toi de
trancher, regarde `interactions` pour voir ce que l'agent a remonté.

⚠️ La désinscription est traitée avec un biais volontaire vers la prudence
(le prompt classe en `desinscription` au moindre doute), parce que c'est
une obligation légale, pas une option — voir le point RGPD/CPCE qu'on avait
discuté au tout début.

## Agent d'envoi (email rédigé par Claude, recherche d'actualité incluse, validation humaine obligatoire)

`agents/email_sender.py` prend chaque prospect qualifié et **fait rédiger
l'email par Claude**, spécifiquement pour lui. Avant de rédiger, Claude
**cherche sur le web une actualité récente et pertinente sur l'entreprise**
du prospect (levée de fonds, recrutement clé, expansion...) et s'en sert
si elle est réelle et solide — jamais d'actualité inventée. Il te montre
le résultat, et **n'envoie que si tu tapes "o"**.

La recherche passe par le même `ANTHROPIC_API_KEY` que le reste — pas de
clé supplémentaire à récupérer. Facturée à l'usage par Anthropic, coût
négligeable au volume dont tu parles (quelques dizaines de prospects à la
fois).

### Avant le premier envoi réel

Relis (et ajuste si besoin) `config/email_brief.yaml` — c'est ce fichier
qui pilote le ton et la structure de ce que Claude va écrire.

### Usage

```bash
python3 -m agents.email_sender --dry-run       # teste tout le flux, zéro appel API réel
python3 -m agents.email_sender --limit 5       # revue réelle, limitée à 5 prospects
python3 -m agents.email_sender                 # revue réelle, tous les prospects qualifiés
```

Chaque envoi confirmé passe le statut du prospect à `contacte` et log
l'interaction. Un refus (`n`) ne change rien — le prospect reste dans la
file pour la prochaine fois que tu lances l'agent.

## Manager (orchestrateur)

`manager.py` (à la racine) enchaîne automatiquement les agents sans risque :
qualification puis lecture des emails. Il ne déclenche **jamais** l'envoi
automatiquement — ça reste une action humaine, volontairement.

```bash
python3 -m manager --dry-run    # cycle complet simulé
python3 -m manager              # cycle réel (nécessite ANTHROPIC_API_KEY)
```

À la fin de chaque cycle, il te dit combien de prospects attendent une
décision humaine (envoi ou suite à donner à une réponse) et te renvoie
vers la bonne commande.

## Interface web (tout par clic, sans Terminal)

```bash
python3 -m dashboard.app
```

Puis ouvre **http://127.0.0.1:5001**. C'est le point d'entrée pensé pour
que quelqu'un de pas à l'aise avec le Terminal puisse utiliser tout le
système — la seule commande à taper est celle du dessus pour démarrer le
serveur, tout le reste se fait par clic dans le navigateur.

⚠️ Le port est **5001**, pas 5000 : sur macOS, AirPlay Receiver squatte
le 5000 depuis Monterey et fait planter Flask dessus pour une raison qui
n'a rien à voir avec le code.

⚠️ Cette interface ne tourne qu'en local (`127.0.0.1`) — jamais accessible
depuis l'extérieur, pas de risque qu'un tiers y accède via le réseau.

**Tableau de bord** (`/`) — funnel par statut, liste des prospects,
et deux boutons d'action rapide : qualifier les nouveaux prospects,
vérifier les réponses email.

**Ajouter des prospects** (`/ajouter`) — formulaire pour en ajouter un à
la main, ou import CSV en masse (remplace `scripts.cli` et
`scripts.import_csv` pour qui préfère ne pas toucher au Terminal).

**Envoyer des emails** (`/envoi`) — le cœur du système. Pour chaque
prospect qualifié : un bouton "Générer le brouillon" (Claude rédige,
recherche d'actualité incluse), puis "Envoyer" ou "Passer". Rien ne part
sans le clic sur "Envoyer" — même garde-fou que la version Terminal,
juste avec des boutons.

**Paramètres** (`/parametres`) — modifier l'ICP (ce qu'on vend, à qui) et
le ton des emails sans toucher aux fichiers YAML directement, plus un
bouton pour tester la connexion Gmail (utile au tout premier lancement :
un onglet de navigateur s'ouvre automatiquement pour l'autorisation).

Les scripts en ligne de commande (`agents.qualification`,
`agents.email_sender`, `manager.py`...) continuent de fonctionner comme
avant — l'interface web les appelle directement, elle ne les remplace
pas, elle ajoute juste un autre moyen d'y accéder.

## LinkedIn — pas de code, utilise Octopus CRM directement

Après discussion, LinkedIn n'a pas besoin d'être piloté depuis ce projet :
tu veux juste insérer prénom/nom/entreprise dans un message fixe (pas de
rédaction par Claude nécessaire), donc un outil no-code suffit largement
et coûte moins cher qu'une intégration API sur mesure.

Utilise **Octopus CRM** (~10-15$/mois selon le plan) directement depuis
son interface : importe ta sélection de contacts, personnalise avec leurs
variables prénom/nom/entreprise/poste, lance la campagne. Aucun lien avec
ce projet — c'est volontaire, pour rester simple et pas cher.

Si un jour tu veux que la sélection des contacts LinkedIn soit pilotée par
la qualification Claude (comme pour l'email), Unipile reste la bonne
option technique pour une vraie intégration API — mais ce n'est plus le
besoin actuel.

## Et après ?

Dans l'ordre qu'on avait posé au départ (adapté à ce qui s'est précisé en
cours de route) :

1. ✅ Schéma de base + agent qualification
2. ✅ Agent email en lecture seule
3. ✅ Agent email en envoi, rédigé par Claude, avec validation humaine
4. LinkedIn : géré hors-projet via Octopus CRM (pas de code nécessaire)
5. ~~Agent réseaux sociaux~~ (pas nécessaire, d'après toi)
6. ✅ Manager + dashboard (couvre qualification + email)

Reste en option, pas construit : la synchronisation HubSpot (vue CRM en
miroir de ce que fait Gmail).
