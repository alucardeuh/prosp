"""
Agent d'envoi — VALIDATION HUMAINE OBLIGATOIRE, email rédigé par Claude.

Contrairement à l'agent LinkedIn (où de simples variables prénom/entreprise
suffisent), chaque email ici est réellement rédigé par Claude pour ce
prospect précis — pas un template avec des trous remplis, un texte
différent à chaque fois, construit à partir du profil du prospect +
l'ICP (config/icp.yaml) + un brief de ton/structure (config/email_brief.yaml).
Tu valides chaque email avant l'envoi, exactement comme avant.

Usage :
    python3 -m agents.email_sender                 # revue interactive, envoi réel après validation
    python3 -m agents.email_sender --dry-run        # simule tout le flux sans Gmail ni API Claude
    python3 -m agents.email_sender --limit 5        # revue réelle, limitée à 5 prospects
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import database as db  # noqa: E402
from integrations import gmail_client  # noqa: E402

MODEL = "claude-sonnet-5"
ICP_PATH = Path(__file__).parent.parent / "config" / "icp.yaml"
BRIEF_PATH = Path(__file__).parent.parent / "config" / "email_brief.yaml"

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


def load_icp(path: Path = ICP_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_brief(path: Path = BRIEF_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_prompt(prospect: dict, icp: dict, brief: dict) -> str:
    produit = icp.get("produit", {})
    return f"""Tu es un agent qui rédige des emails de prospection B2B
personnalisés. Appelle l'outil `rediger_email` avec ton résultat, ne
réponds jamais en texte libre.

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
n'importe qui. Appuie-toi sur un détail réel de son profil ci-dessus."""


def redact_email(prospect: dict, icp: dict, brief: dict, client=None) -> dict:
    """Appelle Claude pour rédiger l'email. Retourne {objet, corps}."""
    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        tools=[TOOL_REDACTION],
        tool_choice={"type": "tool", "name": "rediger_email"},
        messages=[{"role": "user", "content": build_prompt(prospect, icp, brief)}],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "rediger_email":
            return block.input
    raise RuntimeError("L'API n'a pas retourné d'appel d'outil (réponse inattendue).")


def _fake_redaction(prospect: dict) -> dict:
    return {
        "objet": f"[DRY-RUN] Objet simulé pour {prospect.get('prenom', '')}",
        "corps": "[DRY-RUN] Corps d'email simulé, aucun appel API réel n'a été fait.",
    }


def afficher_brouillon(prospect: dict, objet: str, corps: str) -> None:
    print("\n" + "=" * 70)
    print(f"À : {prospect.get('prenom', '')} {prospect.get('nom', '')} <{prospect.get('email', '')}>")
    print(f"Objet : {objet}")
    print("-" * 70)
    print(corps)
    print("=" * 70)


def demander_validation(dry_run: bool) -> str:
    """Retourne 'oui', 'non' ou 'quitter'. En --dry-run, auto-valide."""
    if dry_run:
        print("[DRY-RUN] validation automatique (oui) pour tester le flux, aucun email réel envoyé")
        return "oui"
    reponse = input("Envoyer cet email ? [o]ui / [n]on / [q]uitter : ").strip().lower()
    if reponse in ("o", "oui", "y", "yes"):
        return "oui"
    if reponse in ("q", "quit", "quitter"):
        return "quitter"
    return "non"


def run(dry_run: bool = False, limit: int | None = None) -> None:
    icp = load_icp()
    brief = load_brief()

    prospects = db.list_prospects(statut="qualifie")
    if limit:
        prospects = prospects[:limit]

    if not prospects:
        print("Aucun prospect qualifié en attente d'envoi.")
        return

    service = None if dry_run else gmail_client.get_service()
    envoyes, refuses = 0, 0

    for p in prospects:
        if not p.get("email"):
            print(f"  [{p['id']}] {p.get('prenom', '')} {p.get('nom', '')} : pas d'email, ignoré.", file=sys.stderr)
            continue

        redaction = _fake_redaction(p) if dry_run else redact_email(p, icp, brief)
        objet, corps = redaction["objet"], redaction["corps"]

        afficher_brouillon(p, objet, corps)
        decision = demander_validation(dry_run)
        if decision == "quitter":
            print("Arrêt demandé.")
            break
        if decision == "non":
            print(f"  [{p['id']}] passé, reste 'qualifie' pour une prochaine revue.")
            refuses += 1
            continue

        try:
            if not dry_run:
                gmail_client.send_message(service, p["email"], objet, corps)
            db.update_statut(p["id"], "contacte")
            db.add_interaction(p["id"], "email_envoye", f"Objet: {objet}")
            envoyes += 1
            print(f"  [{p['id']}] ✅ envoyé, statut -> contacte")
        except Exception as exc:  # noqa: BLE001 - continuer sur les autres prospects
            print(f"  [{p['id']}] ERREUR d'envoi : {exc}", file=sys.stderr)

    print(f"\n{envoyes} envoyé(s), {refuses} passé(s).")


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="Agent d'envoi (email rédigé par Claude, validation humaine obligatoire)")
    parser.add_argument("--dry-run", action="store_true", help="simule tout sans Gmail ni API Claude")
    parser.add_argument("--limit", type=int, default=None, help="ne traiter que les N premiers prospects qualifiés")
    args = parser.parse_args()

    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY n'est pas définie. Utilise --dry-run pour tester sans clé.", file=sys.stderr)
        sys.exit(1)

    db.init_db()
    try:
        run(dry_run=args.dry_run, limit=args.limit)
    except FileNotFoundError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)
