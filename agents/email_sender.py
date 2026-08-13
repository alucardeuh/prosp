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
import profils  # noqa: E402
from db import database as db  # noqa: E402
from integrations import gmail_client  # noqa: E402

# Identifiant de modèle de l'API Anthropic. Surchargeable via .env (CLAUDE_MODEL=...).
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

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


def _max_recherches_web() -> int:
    try:
        return max(0, int(db.get_reglage("max_recherches_web") or 3))
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


def build_prompt(prospect: dict, icp: dict, brief: dict, avec_recherche: bool = True) -> str:
    produit = icp.get("produit", {})

    if avec_recherche:
        bloc_redaction = f"""# Étape 1 — recherche (obligatoire avant de rédiger)
Cherche sur le web une actualité récente et pertinente sur l'entreprise
"{prospect.get('entreprise', '')}" (levée de fonds, recrutement clé,
expansion, nouveau produit, résultats financiers, changement de
direction...). N'utilise cette info QUE si elle est réelle et solide —
ne mentionne jamais une actualité inventée ou incertaine. Si tu ne
trouves rien de fiable, base-toi uniquement sur le profil du prospect
ci-dessous, sans forcer une actualité qui n'existe pas.

# Étape 2 — rédaction
Une fois ta recherche faite, appelle l'outil `rediger_email` avec ton
résultat final. Ne réponds jamais en texte libre à la fin."""
    else:
        bloc_redaction = """# Rédaction
La recherche web est désactivée pour cet envoi (réglage du profil dans
Paramètres) : base-toi uniquement sur le profil du prospect ci-dessous,
sans jamais inventer une actualité que tu n'as pas vérifiée. Appelle
l'outil `rediger_email` avec ton résultat final. Ne réponds jamais en
texte libre à la fin."""

    return f"""Tu es un agent qui rédige des emails de prospection B2B
personnalisés.

{bloc_redaction}

# Ce qu'on vend
{produit.get('description', '')}
Proposition de valeur : {produit.get('proposition_de_valeur', '')}

# Ton et structure attendus
Ton : {brief.get('ton', '')}
Longueur max : {brief.get('longueur_max_mots', 150)} mots
Structure : {brief.get('structure_attendue', '')}

# Ce prospect précis
Prénom : {prospect.get('prenom', '')}
Nom : {prospect.get('nom', '')}
Poste : {prospect.get('poste', '')}
Entreprise : {prospect.get('entreprise', '')}
Secteur : {prospect.get('secteur', '')}
Taille d'entreprise : {prospect.get('taille_entreprise', '')}
Notes : {prospect.get('notes', '')}
Raison de qualification (pourquoi ce prospect a été retenu) : {prospect.get('raison_qualification', '')}

# Obligatoire à la fin de l'email
{brief.get('signature', '')}
{brief.get('mention_obligatoire', '')}

Rédige un email qui montre concrètement qu'on connaît la situation de CE
prospect précis — pas un email générique qui pourrait être envoyé à
n'importe qui. Appuie-toi sur l'actualité trouvée si elle est pertinente,
sinon sur un détail réel de son profil ci-dessus."""


def build_prompt_relance(prospect: dict, icp: dict, brief: dict, premier_email: str) -> str:
    produit = icp.get("produit", {})
    return f"""Tu es un agent qui rédige des emails de RELANCE de
prospection B2B. Le prospect a déjà reçu un premier email resté sans
réponse — tu rédiges le suivi.

Appelle l'outil `rediger_email` avec ton résultat. Ne réponds jamais en
texte libre.

# Règles d'une bonne relance
- BEAUCOUP plus courte que le premier email (60-80 mots maximum).
- Fait naturellement référence au message précédent sans le paraphraser.
- Apporte un angle NOUVEAU (un bénéfice concret, une question directe,
  une info utile) — jamais un simple "je me permets de revenir vers vous".
- Aucune culpabilisation, aucun reproche de non-réponse.
- Se termine par une question simple à laquelle il est facile de répondre.
- L'objet reprend le fil : "Re: <objet initial>" si un objet initial est
  identifiable dans le premier email ci-dessous, sinon un objet court.

# Ce qu'on vend
{produit.get('description', '')}
Proposition de valeur : {produit.get('proposition_de_valeur', '')}

# Ton
{brief.get('ton', '')}

# Ce prospect
{prospect.get('prenom', '')} {prospect.get('nom', '')} — {prospect.get('poste', '')} chez {prospect.get('entreprise', '')}
Relances déjà envoyées : {prospect.get('nb_relances', 0)}

# Premier email envoyé (pour référence, ne pas le répéter)
{premier_email or '(contenu du premier email non disponible — reste générique sur la référence au message précédent)'}

# Obligatoire à la fin de l'email
{brief.get('signature', '')}
{brief.get('mention_obligatoire', '')}"""


def _appeler_redaction(prompt: str, client=None, max_recherches: int = 0) -> dict:
    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    tools = [TOOL_REDACTION]
    if max_recherches > 0:
        tools = [_outil_recherche_web(max_recherches), TOOL_REDACTION]

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        tools=tools,
        messages=[{"role": "user", "content": prompt}],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "rediger_email":
            return block.input
    raise RuntimeError(
        "L'API n'a pas appelé rediger_email (elle a peut-être répondu en texte "
        "libre au lieu de finaliser l'email)."
    )


def redact_email(prospect: dict, icp: dict, brief: dict, client=None) -> dict:
    """Rédige l'email initial. Fait de 0 à N recherches web selon le réglage
    'max_recherches_web' (Paramètres, 0 = désactivé) — jamais codé en dur.
    Retourne {objet, corps}."""
    max_recherches = _max_recherches_web()
    prompt = build_prompt(prospect, icp, brief, avec_recherche=max_recherches > 0)
    return _appeler_redaction(prompt, client, max_recherches=max_recherches)


def redact_relance(prospect: dict, icp: dict, brief: dict, client=None) -> dict:
    """Rédige un email de relance court, basé sur le premier email envoyé.
    Pas de recherche web : la relance s'appuie sur le fil existant."""
    derniere = db.derniere_interaction(prospect["id"], "email_envoye")
    premier_email = derniere["contenu"] if derniere else ""
    return _appeler_redaction(
        build_prompt_relance(prospect, icp, brief, premier_email), client, max_recherches=0
    )


def _fake_redaction(prospect: dict, relance: bool = False) -> dict:
    prefixe = "Relance simulée" if relance else "Objet simulé"
    return {
        "objet": f"[DRY-RUN] {prefixe} pour {prospect.get('prenom', '')}",
        "corps": "[DRY-RUN] Corps d'email simulé, aucun appel API réel n'a été fait.",
    }


def generer_brouillon(prospect: dict, icp: dict, brief: dict,
                      type_: str = "initial", dry_run: bool = False, client=None) -> dict:
    """Génère un brouillon (initial ou relance) et le PERSISTE en base.
    Brique utilisée par le CLI et par les jobs du dashboard."""
    if dry_run:
        redaction = _fake_redaction(prospect, relance=(type_ == "relance"))
    elif type_ == "relance":
        redaction = redact_relance(prospect, icp, brief, client)
    else:
        redaction = redact_email(prospect, icp, brief, client)
    db.set_brouillon(prospect["id"], redaction["objet"], redaction["corps"], type_=type_)
    return redaction


def envoyer_brouillon(prospect_id: int, dry_run: bool = False, service=None) -> dict:
    """Envoie le brouillon en attente d'un prospect, avec tous les garde-fous :
    - refuse si le prospect est désinscrit (obligation légale)
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
            service = gmail_client.get_service()
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
                          gmail_thread_id=nouveau_thread_id, rfc_message_id=nouveau_rfc_id)
        db.incrementer_relances(prospect_id)
    else:
        db.add_interaction(prospect_id, "email_envoye", contenu_historique,
                          gmail_thread_id=nouveau_thread_id, rfc_message_id=nouveau_rfc_id)
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

    service = None if dry_run else gmail_client.get_service()
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
