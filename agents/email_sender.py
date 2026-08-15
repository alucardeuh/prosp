"""
Agent d'envoi — VALIDATION HUMAINE OBLIGATOIRE, email rédigé par Claude.

Chaque email est réellement rédigé par Claude pour ce prospect précis —
pas un template avec des trous. Avant de rédiger, Claude cherche sur le
web une actualité récente sur l'entreprise du prospect et ne s'en sert
que si elle est réelle. Rien ne part sans validation humaine.

Gère aussi les RELANCES : un email de suivi court pour les prospects
contactés restés sans réponse, qui fait référence au premier message.

Usage CLI (l'interface web reste le mode principal) :
    python3 -m agents.email_sender                 # revue interactive, envoi réel après validation
    python3 -m agents.email_sender --dry-run        # simule tout le flux
    python3 -m agents.email_sender --limit 5        # revue limitée à 5 prospects
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Voir agents/qualification.py pour l'explication : ce module peut être
# importé sans jamais passer par `if __name__ == "__main__"`, donc c'est
# ici qu'il faut charger .env pour que CLAUDE_MODEL soit bien pris en compte.
load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))
import email_verification  # noqa: E402
import profils  # noqa: E402
from agents import qualification  # noqa: E402 - réutilise _usage_de, pas de duplication
from db import database as db  # noqa: E402
from integrations import gmail_client  # noqa: E402

# Identifiant de modèle de l'API Anthropic. Surchargeable via .env (CLAUDE_MODEL=...).
# Modèle de rédaction : contrairement à la qualification et au classement
# des réponses, ici la qualité du texte compte vraiment — c'est ce qui part
# chez un vrai prospect. claude-sonnet-5 (dernière génération Sonnet) est à
# la fois plus récent ET moins cher que l'ancien défaut claude-sonnet-4-6 —
# pas de compromis qualité/coût ici, juste une mise à jour. Surchargeable
# via .env (CLAUDE_MODEL=...).
# Modèle de rédaction : contrairement à la qualification et au classement
# des réponses, ici la qualité du texte compte vraiment — c'est ce qui part
# chez un vrai prospect. claude-sonnet-5 (dernière génération Sonnet) est à
# la fois plus récent ET moins cher que l'ancien défaut claude-sonnet-4-6 —
# pas de compromis qualité/coût ici, juste une mise à jour. Lu à CHAQUE
# appel pour qu'un changement fait depuis Paramètres prenne effet tout de
# suite, sans redémarrer l'app. Surchargeable via .env (CLAUDE_MODEL=...).
def _modele() -> str:
    return os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")

def _outil_recherche_web(max_uses: int) -> dict:
    """Outil serveur Anthropic : Claude décide seul quand chercher, l'API
    exécute la recherche — aucune clé supplémentaire, même ANTHROPIC_API_KEY.
    max_uses vient du réglage 'max_recherches_web' (Paramètres), pas codé en
    dur : chaque recherche coûte $10/1000 + le coût en tokens du contenu
    rapporté, donc c'est un curseur à ajuster, pas une constante."""
    return {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": max_uses,
    }


# Niveaux nommés pour le choix du nombre de recherches web — utilisés à la
# fois comme valeur par défaut (Paramètres) et comme choix ponctuel par lot
# d'envoi (page /envoi, remplace la valeur par défaut pour CE lot précis).
# Nommés plutôt qu'un simple chiffre : plus clair à choisir au moment de
# lancer un envoi que de se souvenir de ce que "3" veut dire.
NIVEAUX_RECHERCHE = {
    "desactive": 0,
    "simple": 1,
    "normal": 3,
    "approfondi": 5,
}


def _max_recherches_web(niveau: str | int | None = None) -> int:
    """Résout un niveau de recherche vers un nombre de recherches. Un entier
    est utilisé tel quel (compat CLI/anciens appels). Un nom (simple/normal/
    approfondi/desactive) est traduit. None retombe sur le réglage par défaut
    du profil (Paramètres)."""
    if niveau is None:
        try:
            return max(0, int(db.get_reglage("max_recherches_web") or 3))
        except (TypeError, ValueError):
            return 3
    if isinstance(niveau, str):
        return NIVEAUX_RECHERCHE.get(niveau, 3)
    try:
        return max(0, int(niveau))
    except (TypeError, ValueError):
        return 3

TOOL_REDACTION = {
    "name": "rediger_email",
    "description": "Rédige un email de prospection personnalisé pour ce prospect précis.",
    "input_schema": {
        "type": "object",
        "properties": {
            "objet": {"type": "string", "description": "Objet de l'email, court et concret."},
            "corps": {"type": "string", "description": "Corps complet de l'email, prêt à envoyer tel quel."},
        },
        "required": ["objet", "corps"],
    },
}


# Règles d'écriture qui s'appliquent à TOUT email envoyé, quel que soit le
# profil (SAMMPO, médical, ou un futur profil) — ce sont des règles de fond
# sur la qualité et la délivrabilité du texte, pas des préférences de ton
# négociables comme celles du brief. Définies une seule fois ici plutôt que
# dans chaque email_brief.yaml pour ne jamais avoir à les dupliquer ou les
# oublier sur un nouveau profil.
REGLES_ECRITURE = """# Règles d'écriture (s'appliquent à TOUT email, sans exception)

## Délivrabilité
Aucun mot ni tournure à risque spam : pas de "GRATUIT", "URGENT",
majuscules intégrales, points d'exclamation multiples, ni excès de liens
(un seul lien maximum, seulement s'il est vraiment utile).

## Humaniser
Phrases courtes, une idée par paragraphe, jamais de bloc dense. Aucun
jargon marketing ("solution innovante", "leader du marché", "révolutionner
votre..." sont interdits) — décris un problème concret, pas un
argumentaire. Écris comme si tu démarrais une conversation avec une
personne précise, pas comme si tu diffusais une annonce. Une formulation
trop parfaite et trop symétrique se lit comme un template : préfère une
tournure un peu plus directe et naturelle.

## Objet (si tu dois en générer un)
4 à 8 mots, spécifique à ce prospect précis plutôt que vendeur. Jamais de
majuscules, ponctuation excessive ou emoji.

## Une seule idée, une seule demande
Un email = une idée, une question. Ne mélange jamais deux sujets ou deux
demandes, même reformulées.

## Le call to action
Un seul CTA, à la toute fin, isolé dans sa propre phrase. Formule-le en
question à faible engagement, facile à répondre en un mot ("Ça vous
intéresse d'en discuter 15 minutes ?" plutôt qu'un lien de réservation ou
une formule passive-vague comme "n'hésitez pas à me contacter").

## Registre
Vouvoiement systématique, sans exception. Professionnel et direct, jamais
guindé : verbes actifs ("je vous propose" plutôt que "il serait
envisageable de vous proposer"). Ne mélange jamais les registres dans un
même email (pas d'humour au milieu d'un email factuel, pas de familiarité
soudaine après un paragraphe formel)."""


def build_system_email(icp: dict, brief: dict, avec_recherche: bool = True,
                       contexte_batch: str = "", modeles: list[dict] | None = None) -> list[dict]:
    """2 points de cache :
    - bloc profil (produit, ton, règles d'écriture, signature, exemples) :
      identique pour TOUT email initial de ce profil, quel que soit le lot —
      reste en cache à travers plusieurs lots tant qu'ils s'enchaînent dans
      les 5 minutes (ou 1h si un jour on active le cache longue durée).
    - bloc lot (recherche on/off, contexte de ce lot précis) : change d'un
      lot à l'autre, mais identique pour tous les prospects D'UN MÊME
      lot — cacheable entre les prospects de CE lot.
    Sur un lot de N emails, seul le premier appel paie le plein tarif pour
    ces deux blocs ; les N-1 suivants les lisent en cache à ~10% du prix."""
    produit = icp.get("produit", {})
    bloc_profil = f"""Tu es un agent qui rédige des emails de prospection B2B
personnalisés.

{REGLES_ECRITURE}

# Ce qu'on vend
{produit.get('description', '')}
Proposition de valeur : {produit.get('proposition_de_valeur', '')}

# Ton et structure attendus
Ton : {brief.get('ton', '')}
Longueur max : {brief.get('longueur_max_mots', 150)} mots
Structure : {brief.get('structure_attendue', '')}

# Obligatoire à la fin de chaque email
{brief.get('signature', '')}
{brief.get('mention_obligatoire', '')}"""

    if modeles:
        blocs_exemples = "\n\n".join(
            f"Exemple {i+1}{' — ' + m['titre'] if m.get('titre') else ''}\n"
            f"Objet : {m.get('objet', '')}\n{m.get('corps', '')}"
            for i, m in enumerate(modeles)
        )
        bloc_profil += f"""

# Exemples d'emails qui ont déjà bien fonctionné
Inspire-toi de leur ton, de leur structure et de leur niveau de
personnalisation pour rédiger CET email — ce sont des repères de style,
jamais un texte à recopier tel quel : chaque email doit rester unique et
vraiment écrit pour CE prospect précis.

{blocs_exemples}"""

    if avec_recherche:
        bloc_lot = """# Étape 1 — recherche (obligatoire avant de rédiger)
Cherche sur le web une actualité récente et pertinente sur l'entreprise du
prospect (levée de fonds, recrutement clé, expansion, nouveau produit,
résultats financiers, changement de direction...). N'utilise cette info QUE
si elle est réelle et solide — ne mentionne jamais une actualité inventée ou
incertaine. Si tu ne trouves rien de fiable, base-toi uniquement sur le
profil du prospect, sans forcer une actualité qui n'existe pas.

# Étape 2 — rédaction
Une fois ta recherche faite, appelle l'outil `rediger_email` avec ton
résultat final. Ne réponds jamais en texte libre à la fin."""
    else:
        bloc_lot = """# Rédaction
La recherche web est désactivée pour ce lot (réglage choisi pour cet envoi) :
base-toi uniquement sur le profil du prospect, sans jamais inventer une
actualité que tu n'as pas vérifiée. Appelle l'outil `rediger_email` avec ton
résultat final. Ne réponds jamais en texte libre à la fin."""

    if contexte_batch and contexte_batch.strip():
        bloc_lot += f"""

# Contexte donné pour ce lot d'envoi précis (par la personne qui a lancé
cette génération — à prendre en compte en priorité, ça décrit l'angle ou
la raison de cette campagne précise)
{contexte_batch.strip()}"""

    return [
        {"type": "text", "text": bloc_profil, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": bloc_lot, "cache_control": {"type": "ephemeral"}},
    ]


def build_prompt(prospect: dict) -> str:
    """Partie du prompt PROPRE à ce prospect — jamais mise en cache."""
    return f"""# Ce prospect précis
Prénom : {prospect.get('prenom', '')}
Nom : {prospect.get('nom', '')}
Poste : {prospect.get('poste', '')}
Entreprise : {prospect.get('entreprise', '')}
Secteur : {prospect.get('secteur', '')}
Taille d'entreprise : {prospect.get('taille_entreprise', '')}
Notes : {prospect.get('notes', '')}
Raison de qualification (pourquoi ce prospect a été retenu) : {prospect.get('raison_qualification', '')}

Rédige un email qui montre concrètement qu'on connaît la situation de CE
prospect précis — pas un email générique qui pourrait être envoyé à
n'importe qui. Appuie-toi sur l'actualité trouvée si elle est pertinente,
sinon sur un détail réel de son profil ci-dessus."""


def build_system_relance(icp: dict, brief: dict, contexte_batch: str = "") -> list[dict]:
    """Même logique à 2 points de cache que build_system_email, adaptée aux
    relances (jamais de recherche web, donc pas de variante recherche
    on/off à gérer dans le bloc lot)."""
    produit = icp.get("produit", {})
    bloc_profil = f"""Tu es un agent qui rédige des emails de RELANCE de
prospection B2B. Le prospect a déjà reçu un premier email resté sans
réponse — tu rédiges le suivi.

Appelle l'outil `rediger_email` avec ton résultat. Ne réponds jamais en
texte libre.

{REGLES_ECRITURE}

# Règles supplémentaires propres à la relance
- BEAUCOUP plus courte que le premier email (60-80 mots maximum).
- Fait naturellement référence au message précédent sans le paraphraser.
- Apporte un angle NOUVEAU (un bénéfice concret, une question directe,
  une info utile) — jamais un simple "je me permets de revenir vers vous".
- Aucune culpabilisation, aucun reproche de non-réponse.
- Se termine par une question simple à laquelle il est facile de répondre.
- L'objet reprend le fil : "Re: <objet initial>" si un objet initial est
  identifiable dans le premier email, sinon un objet court.

# Ce qu'on vend
{produit.get('description', '')}
Proposition de valeur : {produit.get('proposition_de_valeur', '')}

# Ton
{brief.get('ton', '')}

# Obligatoire à la fin de chaque email
{brief.get('signature', '')}
{brief.get('mention_obligatoire', '')}"""

    bloc_lot = "(aucun contexte de lot particulier pour ces relances)"
    if contexte_batch and contexte_batch.strip():
        bloc_lot = f"""# Contexte donné pour ce lot de relances précis (par la personne qui a
lancé cette génération — à prendre en compte en priorité)
{contexte_batch.strip()}"""

    return [
        {"type": "text", "text": bloc_profil, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": bloc_lot, "cache_control": {"type": "ephemeral"}},
    ]


def build_prompt_relance(prospect: dict, premier_email: str) -> str:
    """Partie propre à ce prospect — jamais mise en cache."""
    return f"""# Ce prospect
{prospect.get('prenom', '')} {prospect.get('nom', '')} — {prospect.get('poste', '')} chez {prospect.get('entreprise', '')}
Relances déjà envoyées : {prospect.get('nb_relances', 0)}

# Premier email envoyé (pour référence, ne pas le répéter)
{premier_email or '(contenu du premier email non disponible — reste générique sur la référence au message précédent)'}"""


def _appeler_redaction(system: list[dict], prompt: str, client=None, max_recherches: int = 0) -> tuple[dict, dict]:
    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    tools = [TOOL_REDACTION]
    if max_recherches > 0:
        tools = [_outil_recherche_web(max_recherches), TOOL_REDACTION]

    response = client.messages.create(
        model=_modele(),
        max_tokens=4096,
        tools=tools,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    usage = qualification._usage_de(response)
    for block in response.content:
        if block.type == "tool_use" and block.name == "rediger_email":
            return block.input, usage
    raise RuntimeError(
        "L'API n'a pas appelé rediger_email (elle a peut-être répondu en texte "
        "libre au lieu de finaliser l'email)."
    )


def redact_email(prospect: dict, icp: dict, brief: dict, client=None,
                 niveau_recherche: str | int | None = None, contexte_batch: str = "") -> tuple[dict, dict]:
    """Rédige l'email initial. Fait de 0 à N recherches web selon
    niveau_recherche (simple/normal/approfondi/desactive, ou un entier, ou
    None pour le réglage par défaut du profil dans Paramètres). contexte_batch
    est un texte libre propre à CE lot d'envoi, jamais persisté ailleurs que
    dans le prompt de cette génération précise. Retourne ({objet, corps}, usage)."""
    max_recherches = _max_recherches_web(niveau_recherche)
    modeles = profils.load_modeles(prospect.get("profil") or db.profil_actif())
    system = build_system_email(icp, brief, avec_recherche=max_recherches > 0,
                                contexte_batch=contexte_batch, modeles=modeles)
    return _appeler_redaction(system, build_prompt(prospect), client, max_recherches=max_recherches)


def redact_relance(prospect: dict, icp: dict, brief: dict, client=None,
                   contexte_batch: str = "") -> tuple[dict, dict]:
    """Rédige un email de relance court, basé sur le premier email envoyé.
    Pas de recherche web : la relance s'appuie sur le fil existant."""
    derniere = db.derniere_interaction(prospect["id"], "email_envoye")
    premier_email = derniere["contenu"] if derniere else ""
    system = build_system_relance(icp, brief, contexte_batch=contexte_batch)
    return _appeler_redaction(system, build_prompt_relance(prospect, premier_email), client, max_recherches=0)


def _fake_redaction(prospect: dict, relance: bool = False) -> tuple[dict, dict]:
    prefixe = "Relance simulée" if relance else "Objet simulé"
    return {
        "objet": f"[DRY-RUN] {prefixe} pour {prospect.get('prenom', '')}",
        "corps": "[DRY-RUN] Corps d'email simulé, aucun appel API réel n'a été fait.",
    }, {"tokens_entree": 0, "tokens_sortie": 0, "recherches_web": 0}


def _verifier_email_prospect(prospect: dict) -> dict:
    """Vérifie l'email d'un prospect en s'appuyant sur le résultat mis en
    cache en base (colonne email_verifie) — ne relance une vraie
    vérification DNS que si cette adresse précise n'a jamais été vérifiée
    (le cache est remis à zéro automatiquement dès que l'email change,
    voir db.update_prospect). Retourne {statut, raison}."""
    statut_cache = prospect.get("email_verifie")
    if statut_cache:
        return {"statut": statut_cache, "raison": "(résultat déjà vérifié, mis en cache)"}
    resultat = email_verification.verifier(prospect.get("email"))
    db.set_email_verifie(prospect["id"], resultat["statut"])
    return resultat


def generer_brouillon(prospect: dict, icp: dict, brief: dict,
                      type_: str = "initial", dry_run: bool = False, client=None,
                      niveau_recherche: str | int | None = None, contexte_batch: str = "") -> dict:
    """Génère un brouillon (initial ou relance) et le PERSISTE en base, avec
    le coût réel (tokens + recherches) de cette génération précise — cumulé
    si le brouillon est régénéré plusieurs fois avant l'envoi.
    niveau_recherche et contexte_batch ne s'appliquent qu'aux emails initiaux
    (une relance ne fait jamais de recherche, et le contexte de batch reste
    pertinent pour elle aussi si fourni). Brique utilisée par le CLI et par
    les jobs du dashboard.

    Vérifie l'email AVANT d'appeler l'API : inutile de payer une rédaction
    pour une adresse dont le domaine est confirmé incapable de recevoir du
    courrier — la vérification est gratuite (DNS), la génération ne l'est pas."""
    if not dry_run:
        verif = _verifier_email_prospect(prospect)
        if verif["statut"] == "invalide":
            raise ValueError(
                f"Email invalide ({prospect.get('email')}) : {verif['raison']} "
                "— génération annulée pour ne pas gaspiller de tokens."
            )
    if dry_run:
        redaction, usage = _fake_redaction(prospect, relance=(type_ == "relance"))
    elif type_ == "relance":
        redaction, usage = redact_relance(prospect, icp, brief, client, contexte_batch=contexte_batch)
    else:
        redaction, usage = redact_email(prospect, icp, brief, client,
                                        niveau_recherche=niveau_recherche, contexte_batch=contexte_batch)
    db.set_brouillon(prospect["id"], redaction["objet"], redaction["corps"], type_=type_,
                     tokens_entree=usage["tokens_entree"], tokens_sortie=usage["tokens_sortie"],
                     recherches_web=usage["recherches_web"])
    return redaction


def envoyer_brouillon(prospect_id: int, dry_run: bool = False, service=None) -> dict:
    """Envoie le brouillon en attente d'un prospect, avec tous les garde-fous :
    - refuse si le prospect est désinscrit (obligation légale)
    - refuse si l'email est confirmé invalide (syntaxe ou domaine sans MX/A)
    - refuse si la limite d'envois du jour est atteinte (délivrabilité)
    - stocke objet + corps dans l'historique (nécessaire aux futures relances)
    - met à jour statut et compteur de relances selon le type de brouillon
    Retourne le brouillon envoyé. Lève ValueError avec un message clair sinon."""
    prospect = db.get_prospect(prospect_id)
    brouillon = db.get_brouillon(prospect_id)
    if not prospect or not brouillon:
        raise ValueError("Pas de brouillon en attente pour ce prospect.")
    if prospect["statut"] == "desinscrit":
        raise ValueError("Ce prospect s'est désinscrit — envoi bloqué (obligation légale).")
    if not prospect.get("email"):
        raise ValueError("Ce prospect n'a pas d'adresse email.")
    if not dry_run:
        # Défense en profondeur : la génération a déjà filtré les adresses
        # invalides, mais le brouillon a pu être créé avant l'ajout de cette
        # vérification, ou l'email édité après coup — on revérifie ici,
        # dernière porte avant un envoi réel.
        verif = _verifier_email_prospect(prospect)
        if verif["statut"] == "invalide":
            raise ValueError(f"Email invalide ({prospect['email']}) : {verif['raison']} — envoi refusé.")

    limite = int(db.get_reglage("limite_envois_jour") or 50)
    if db.envois_du_jour() >= limite:
        raise ValueError(
            f"Limite d'envois du jour atteinte ({limite}). "
            "Réessaie demain ou ajuste la limite dans les paramètres."
        )

    # Pour une relance : récupère le fil Gmail du premier email envoyé, pour
    # que la relance arrive comme la suite de la conversation plutôt que
    # comme un nouveau message isolé dans la boîte du prospect.
    thread_id = rfc_id = None
    if brouillon["type"] == "relance":
        premier = db.derniere_interaction(prospect_id, "email_envoye")
        if premier:
            thread_id = premier.get("gmail_thread_id")
            rfc_id = premier.get("rfc_message_id")

    reponse_envoi = None
    if not dry_run:
        if service is None:
            # Le profil du prospect, pas le profil "actif" dans l'interface au
            # moment de l'appel : un envoi programmé part en arrière-plan,
            # potentiellement pendant qu'un AUTRE profil est affiché à l'écran.
            service = gmail_client.get_service(prospect.get("profil"))
        reponse_envoi = gmail_client.send_message(
            service, prospect["email"], brouillon["objet"], brouillon["corps"],
            thread_id=thread_id, in_reply_to=rfc_id,
        )

    # Fil du message qu'on vient d'envoyer. Pour un premier email, ça devient
    # l'ancrage de TOUTES ses relances futures (elles cherchent toujours le
    # fil du type 'email_envoye', qui n'existe qu'une fois par prospect) —
    # le threadId Gmail reste valable pour tout le fil, même à la 2e ou 3e
    # relance, donc elles atterrissent bien toutes au même endroit.
    nouveau_thread_id = nouveau_rfc_id = None
    if reponse_envoi:
        nouveau_thread_id = reponse_envoi.get("threadId")
        nouveau_rfc_id = gmail_client.get_rfc_message_id(service, reponse_envoi["id"])

    contenu_historique = f"Objet: {brouillon['objet']}\n\n{brouillon['corps']}"
    if brouillon["type"] == "relance":
        db.add_interaction(prospect_id, "relance_envoyee", contenu_historique,
                          gmail_thread_id=nouveau_thread_id, rfc_message_id=nouveau_rfc_id,
                          tokens_entree=brouillon.get("tokens_entree", 0),
                          tokens_sortie=brouillon.get("tokens_sortie", 0),
                          recherches_web=brouillon.get("recherches_web", 0))
        db.incrementer_relances(prospect_id)
    else:
        db.add_interaction(prospect_id, "email_envoye", contenu_historique,
                          gmail_thread_id=nouveau_thread_id, rfc_message_id=nouveau_rfc_id,
                          tokens_entree=brouillon.get("tokens_entree", 0),
                          tokens_sortie=brouillon.get("tokens_sortie", 0),
                          recherches_web=brouillon.get("recherches_web", 0))
        db.update_statut(prospect_id, "contacte")
    db.delete_brouillon(prospect_id)
    return brouillon


# ---------------------------------------------------------------- CLI

def afficher_brouillon(prospect: dict, objet: str, corps: str) -> None:
    print("\n" + "=" * 70)
    print(f"À : {prospect.get('prenom', '')} {prospect.get('nom', '')} <{prospect.get('email', '')}>")
    print(f"Objet : {objet}")
    print("-" * 70)
    print(corps)
    print("=" * 70)


def demander_validation(dry_run: bool) -> str:
    if dry_run:
        print("[DRY-RUN] validation automatique (oui) pour tester le flux, aucun email réel envoyé")
        return "oui"
    reponse = input("Envoyer cet email ? [o]ui / [n]on / [q]uitter : ").strip().lower()
    if reponse in ("o", "oui", "y", "yes"):
        return "oui"
    if reponse in ("q", "quit", "quitter"):
        return "quitter"
    return "non"


def run(dry_run: bool = False, limit: int | None = None, profil: str | None = None) -> None:
    profil = profil or db.profil_actif()
    icp = profils.load_icp(profil)
    brief = profils.load_brief(profil)

    prospects = [p for p in db.list_prospects(statut="qualifie", profil=profil) if p.get("email")]
    if limit:
        prospects = prospects[:limit]

    if not prospects:
        print(f"Aucun prospect qualifié avec email en attente d'envoi (profil '{profil}').")
        return

    service = None if dry_run else gmail_client.get_service(profil)
    envoyes, refuses = 0, 0

    for p in prospects:
        redaction = generer_brouillon(p, icp, brief, dry_run=dry_run)
        afficher_brouillon(p, redaction["objet"], redaction["corps"])
        decision = demander_validation(dry_run)
        if decision == "quitter":
            print("Arrêt demandé.")
            break
        if decision == "non":
            db.delete_brouillon(p["id"])
            print(f"  [{p['id']}] passé, reste 'qualifie' pour une prochaine revue.")
            refuses += 1
            continue

        try:
            envoyer_brouillon(p["id"], dry_run=dry_run, service=service)
            envoyes += 1
            print(f"  [{p['id']}] ✅ envoyé, statut -> contacte")
        except Exception as exc:  # noqa: BLE001 - continuer sur les autres prospects
            print(f"  [{p['id']}] ERREUR d'envoi : {exc}", file=sys.stderr)

    print(f"\n{envoyes} envoyé(s), {refuses} passé(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent d'envoi (email rédigé par Claude, validation humaine obligatoire)")
    parser.add_argument("--dry-run", action="store_true", help="simule tout sans Gmail ni API Claude")
    parser.add_argument("--limit", type=int, default=None, help="ne traiter que les N premiers prospects qualifiés")
    parser.add_argument("--profil", type=str, default=None, help="profil à utiliser (défaut : profil actif)")
    args = parser.parse_args()

    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY n'est pas définie. Utilise --dry-run pour tester sans clé.", file=sys.stderr)
        sys.exit(1)

    db.init_db()
    try:
        run(dry_run=args.dry_run, limit=args.limit, profil=args.profil)
    except FileNotFoundError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)
