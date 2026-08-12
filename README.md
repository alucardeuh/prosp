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

## Agent d'envoi (validation humaine obligatoire)

`agents/email_sender.py` prend chaque prospect qualifié, génère l'email
depuis `config/template_prospection.yaml`, te l'affiche, et **n'envoie
que si tu tapes explicitement "o"** à l'invite. Trois réponses possibles :
`o` (envoie et passe au suivant), `n` (passe sans envoyer, le prospect
reste `qualifie` pour une prochaine revue), `q` (arrête tout de suite).

### Avant le premier envoi réel

1. Remplis `config/template_prospection.yaml` avec ton vrai message. Le
   script t'avertit si tu as laissé le placeholder.
2. Si tu as déjà autorisé Gmail pour l'agent de lecture seule, **supprime
   `credentials/token.json`** et relance une commande — le scope a changé
   (ajout de `gmail.send`), il faut ré-autoriser.

### Usage

```bash
python3 -m agents.email_sender --dry-run       # teste tout le flux, auto-validé, zéro envoi réel
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

## Dashboard (lecture seule)

```bash
python3 -m dashboard.app
```

Puis ouvre **http://127.0.0.1:5001**. Vue du pipeline (compteurs par
statut, cliquables pour filtrer) + liste des prospects + détail avec
historique complet par prospect. Aucune écriture en base depuis le
dashboard — c'est un outil de suivi, pas encore de pilotage.

⚠️ Le port est **5001**, pas 5000 : sur macOS, AirPlay Receiver squatte
le 5000 depuis Monterey et fait planter Flask dessus pour une raison qui
n'a rien à voir avec le code.

## Et après ?

Dans l'ordre qu'on avait posé :

1. ✅ Schéma de base + agent qualification
2. ✅ Agent email en lecture seule
3. ✅ Agent email en envoi, avec validation humaine
4. Agent LinkedIn (le plus risqué — nécessite de choisir un outil tiers payant)
5. Agent réseaux sociaux
6. ✅ Manager + dashboard (ce qu'on vient d'ajouter, sur le périmètre existant — sera étendu à LinkedIn/réseaux quand ils existeront)

On construit la suite dès que tu veux, dans ce même dossier.
