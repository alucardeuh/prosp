# Prosp — prospection B2B pilotée par Claude

Système de prospection commerciale : qualification des prospects par rapport
à ton ICP, emails rédigés par Claude un par un (avec recherche d'actualité
sur l'entreprise), lecture et classement automatique des réponses Gmail,
relances, import HubSpot, stats — le tout piloté depuis une interface web
locale, sans Terminal. **Rien ne part jamais sans ton clic.**

## Démarrage

```bash
pip3 install -r requirements.txt
cp .env.example .env        # colle ta clé ANTHROPIC_API_KEY dedans
python3 -m dashboard.app
```

Puis ouvre **http://127.0.0.1:5001** (port 5001 : AirPlay squatte le 5000
sur macOS). L'interface ne tourne qu'en local (127.0.0.1), jamais exposée
sur le réseau.

### Sans Terminal (double-clic)

- **macOS** : double-clique `Lancer Prosp.command` (clic droit → Ouvrir la
  première fois, macOS bloque les fichiers téléchargés par défaut).
- **Windows** : double-clique `Lancer Prosp (Windows).bat`. Une fenêtre
  "Prosp - serveur" s'ouvre en arrière-plan (réduite) — ne la ferme pas tant
  que tu utilises l'app, c'est elle qui fait tourner le serveur.

Les deux installent les dépendances tout seuls au premier lancement, puis
ouvrent directement le navigateur sur l'interface.

## Les pages

- **Pipeline** (`/`) — barre de pipeline cliquable par statut, table triable
  (clic sur les en-têtes), recherche instantanée, changement de statut
  directement dans la liste. Boutons « Qualifier les nouveaux » et
  « Vérifier les réponses » : tout tourne en arrière-plan avec une barre de
  progression et un bouton **Annuler**, la page ne gèle plus jamais.
- **Envoi** (`/envoi`) — réglages du lot (niveau de recherche web, contexte
  libre) en haut, puis trois onglets cliquables :
  - **Brouillons** — prêts à relire/envoyer/programmer, en liste compacte
    (clic sur un nom pour déplier son email complet) ;
  - **À générer** — les qualifiés sans brouillon, un par un ou tous d'un coup ;
  - **Mis de côté** — les brouillons passés pour plus tard (bouton « Mettre
    de côté », ex-« Passer ») : jamais supprimés, juste rangés — reprends-les
    ou supprime-les pour de bon depuis cet onglet.

  Plus bas, une **sélection sur mesure** (cases à cocher, filtrable par
  poste / statut / nombre d'envois déjà réalisés — peu importe le statut de
  qualification, mais toujours avec un email, sinon le prospect n'a rien à
  faire dans une table servant à choisir qui recevra un email — il reste
  visible partout ailleurs). Depuis cette même sélection : **supprimer**
  définitivement les prospects choisis. Pour supprimer TOUS les prospects
  d'un profil d'un coup, direction Paramètres → Zone dangereuse
  (confirmation par saisie du nom du profil, irréversible).

  Pour écrire un email à un prospect précis (à la main ou par l'IA) : passe
  par sa **fiche** (`/prospect/N`) plutôt que par cette page — le choix
  « Rédiger un mail vierge » ou « Générer par Claude » s'y trouve, et
  redirige automatiquement vers Envoi une fois le brouillon prêt.
- **Relances** (`/relances`) — prospects contactés depuis plus de N jours
  sans réponse, sans réponse entre-temps. Relances courtes (60-80 mots) qui
  arrivent dans le **même fil Gmail** que le premier email (pas un nouveau
  message isolé), angle nouveau, jamais de recherche web.
- **Ajouter** (`/ajouter`) — un prospect à la main, import CSV en masse
  (reconnaît les intitulés courants même avec accents/majuscules, dont les
  exports HubSpot — gère l'entreprise "principale" quand plusieurs sont
  associées à un contact, capture automatiquement en champ personnalisé
  tout ce qui n'est pas reconnu plutôt que de le perdre, dédoublonne par
  email et par ID HubSpot), et gestion des **champs personnalisés** :
  ajoute ou retire des variables propres à un profil (ex. « Budget estimé »)
  en plus des champs de base — éditables sur chaque fiche.
- **Modèles** (`/modeles`) — emails qui ont déjà bien marché (par profil).
  Claude s'en inspire pour le ton et la structure à chaque rédaction IA
  (jamais recopiés mot pour mot).
- **Stats** (`/stats`) — taux de réponse, taux de qualification, RDV,
  désinscrits, graphe des envois par semaine, et le détail des **tokens
  consommés** (entrée/sortie/recherches web, coût des recherches en $).
- **Paramètres** (`/parametres`) — en tête de page, la **clé API Anthropic**
  et les modèles Claude utilisés, puis les **connexions** (Gmail, HubSpot,
  propres à chaque profil) pilotables entièrement depuis le navigateur ;
  puis ICP, ton des emails, limites d'envoi, délai/nombre de relances,
  création de profils.
- **Fiche prospect** (`/prospect/N`) — champs fixes et personnalisés
  éditables, notes de suivi horodatées, historique complet avec tokens
  consommés par action, suppression.

## Connexions

Tout se règle depuis **Paramètres**, rien à faire en Terminal après la
toute première installation.

### Clé API Anthropic
Commune à tous les profils. Colle ta clé (générée sur
[console.anthropic.com](https://console.anthropic.com/settings/keys)) dans
Paramètres → Clé API Anthropic — écrite dans `.env`, active immédiatement,
pas besoin de redémarrer l'app. Les modèles utilisés (rédaction vs
qualification/classement) sont réglables juste en dessous, avec les mêmes
garanties d'effet immédiat.

### Gmail et HubSpot — propres à CHAQUE profil
SAMMPO et un profil médical n'utilisent probablement pas le même compte
Gmail ni le même compte HubSpot — ces deux connexions sont donc **propres
au profil actif**, pas globales. Change de profil dans la barre latérale
avant de connecter, chaque profil garde les siennes séparément
(`credentials/<profil>/` pour Gmail).

**Gmail** : dépose le `client_secret.json` téléchargé depuis Google Cloud
Console directement dans Paramètres, puis clique « Connecter Gmail » — un
onglet s'ouvre pour l'autorisation. Statut visible en un coup d'œil
(identifiants manquants / prêt / connecté), bouton Déconnecter pour changer
de compte.

**HubSpot** : connexion par **token d'App privée** plutôt qu'un vrai bouton
OAuth — un bouton OAuth demanderait d'enregistrer une app dans un compte
développeur HubSpot séparé, une procédure lourde pensée pour publier une
app à d'autres. L'App privée est le chemin que HubSpot recommande lui-même
pour relier son propre compte à son propre outil :

1. Dans HubSpot : Réglages → Intégrations → Applications privées → Créer.
2. Coche le scope `crm.objects.contacts.read`.
3. Colle le token généré dans Paramètres → HubSpot (profil actif).

Une fois connecté, **« Importer les contacts »** lance un job qui récupère
tous les contacts HubSpot (prénom/nom/poste/entreprise/email) et les ajoute
au profil actif — sans jamais créer de doublon si tu relances l'import plus
tard (dédoublonnage par ID HubSpot, stocké comme champ personnalisé). Import
à sens unique pour l'instant : rien n'est renvoyé vers HubSpot.

*Tu avais déjà Gmail/HubSpot connectés avant cette version ? Ils sont
rattachés automatiquement au profil `sammpo` au premier démarrage — rien à
reconnecter.*

## Multi-profil

Un **profil** = une offre + une cible : son ICP, son ton d'email, ses
champs personnalisés, ses connexions Gmail/HubSpot, ses prospects. Tout vit
dans `config/profils/<nom>/` (+ `credentials/<nom>/` pour Gmail).
Le profil actif se change dans la barre latérale ; chaque prospect
appartient au profil actif au moment de son ajout, et la qualification /
rédaction utilisent toujours la config du profil du moment. Le **quota
d'envois quotidien est partagé entre tous les profils** (une seule boîte
Gmail, une seule réputation d'envoi) — ce n'est pas un oubli, c'est
volontaire.

## Garde-fous (non négociables)

- **Validation humaine** : aucun email ne part sans clic sur « Envoyer ».
- **Quota quotidien** : 50 envois/jour par défaut (tous profils, premiers
  emails + relances confondus), réglable, appliqué côté serveur.
- **Désinscription** : un prospect `desinscrit` est bloqué à l'envoi quoi
  qu'il arrive, et la lecture des réponses classe en désinscription au
  moindre doute (obligation RGPD/CPCE). La mention STOP est imposée dans
  chaque email généré. Les réponses envoyées par le compte lui-même (auto-
  test, ou boucle accidentelle) ne sont jamais confondues avec une vraie
  réponse de prospect.
- **Max relances** : 2 par prospect par défaut, réglable.
- **Un seul job à la fois**, annulable en un clic — pas de génération en
  double en cas de clic répété.

## Coûts et optimisation tokens

- **Abonnement Claude.ai ≠ facturation API.** Ce sont deux systèmes de
  facturation totalement séparés chez Anthropic — même hors Claude Code (seul
  outil officiel autorisé à utiliser l'usage inclus d'un abonnement Pro/Max),
  un abonnement Claude n'a aucune influence sur la facturation API. Ce projet
  consomme toujours les crédits API (Console Anthropic), jamais l'abonnement.
- **Deux modèles différents selon la tâche.** Qualifier un prospect ou
  classer une réponse reçue sont des tâches mécaniques (comparer à une
  grille de critères, choisir une catégorie) — elles utilisent **Haiku**
  par défaut, nettement moins cher. Rédiger un email est la seule tâche où
  la qualité compte vraiment (c'est ce qui part chez un vrai prospect) —
  elle garde **Sonnet**. Réglable séparément via `CLAUDE_MODEL` (rédaction)
  et `CLAUDE_MODEL_RAPIDE` (qualification + classement) dans `.env`.
- **Recherche web réglable** (Paramètres, ou par lot sur `/envoi`) : de
  désactivée à approfondie (0 à 5 recherches par email initial). Chaque
  recherche facture $10/1000 + le coût en tokens du contenu rapporté —
  c'est un poste de coût important, donc réglable plutôt qu'imposé. Les
  relances n'en font jamais.
- **Réflexion (thinking) désactivée par défaut sur la rédaction** —
  Paramètres → Réglages génération. Depuis Sonnet 5, l'API réfléchit avant
  de répondre par défaut dès qu'aucun paramètre `thinking` n'est précisé
  (contrairement à Sonnet 4.6, où l'absence de ce paramètre voulait dire
  "pas de thinking") — un coût invisible sinon, facturé comme tokens de
  sortie sans jamais s'afficher nulle part. Réglable si tu veux comparer la
  qualité avec le thinking activé sur quelques brouillons.
- **Cache de prompt Anthropic** activé sur la qualification et la
  rédaction : la partie identique à travers tous les prospects d'un même
  lot (ICP, produit, règles d'écriture, ton) est mise en cache côté
  Anthropic — seul le premier appel d'un lot paie le plein tarif pour cette
  partie, les suivants la relisent à ~10% du prix. Aucune action requise,
  s'applique automatiquement dès qu'un lot dépasse le seuil minimum
  d'Anthropic (silencieusement ignoré sinon, sans erreur).
- **Vérification email avant génération** : un email dont le domaine est
  confirmé incapable de recevoir du courrier bloque la génération du
  brouillon avant même l'appel API — inutile de payer une rédaction pour
  une adresse qui ne mènera jamais nulle part.
- **Tokens visibles partout** : par email sur `/envoi` et `/relances`, par
  interaction sur la fiche prospect, en tuiles agrégées sur `/stats`.

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
config/profils/<profil>/   icp.yaml + email_brief.yaml + champs.yaml par profil
db/                        schema.sql + database.py (SQLite, tout l'état)
agents/                    qualification, email_sender (+ relances), email_reader
integrations/               gmail_client (OAuth lecture + envoi), hubspot_client (App privée)
dashboard/                 Flask (app.py), jobs en arrière-plan (jobs.py),
                           templates + static (aucune dépendance front)
manager.py                 orchestrateur CLI (qualification + lecture)
```

Modèle Claude : `claude-sonnet-4-6` par défaut, surchargeable via
`CLAUDE_MODEL` dans `.env`. La recherche web des emails passe par l'outil
serveur Anthropic (même clé API).

## LinkedIn

Volontairement hors-projet : Octopus CRM directement (variables
prénom/nom/entreprise dans son interface). Une intégration API (Unipile)
reste possible si un jour la sélection des contacts doit être pilotée par
la qualification Claude.
