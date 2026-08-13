"""
Agent email — LECTURE SEULE.

Rôle : chercher dans Gmail les réponses des prospects en base, les faire
classer par Claude (intéressé / pas intéressé / à relancer / désinscription /
absence du bureau / autre), et écrire le résultat dans notre base.
Ne modifie et n'envoie jamais rien sur Gmail.

Optimisation : au lieu d'une requête Gmail par prospect (lent quand la base
grossit), les adresses sont regroupées par lots de 20 dans une seule requête
`from:(a OR b OR ...)`, puis chaque message trouvé est rattaché à son
prospect via l'en-tête expéditeur.

Usage :
    python3 -m agents.email_reader                  # scan tous les prospects avec email
    python3 -m agents.email_reader --dry-run         # simule sans toucher Gmail ni l'API Claude
    python3 -m agents.email_reader --test-connexion  # vérifie juste que l'OAuth Gmail fonctionne
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

# Voir agents/qualification.py pour l'explication : ce module peut être
# importé sans jamais passer par `if __name__ == "__main__"`, donc c'est
# ici qu'il faut charger .env pour que CLAUDE_MODEL soit bien pris en compte.
load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import database as db  # noqa: E402
from integrations import gmail_client  # noqa: E402

# Identifiant de modèle de l'API Anthropic. Surchargeable via .env (CLAUDE_MODEL=...).
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

TAILLE_LOT_GMAIL = 20  # adresses par requête Gmail groupée

TOOL_CLASSIFICATION = {
    "name": "classifier_email",
    "description": "Classe la réponse email d'un prospect selon son intention.",
    "input_schema": {
        "type": "object",
        "properties": {
            "categorie": {
                "type": "string",
                "enum": [
                    "interesse", "pas_interesse", "a_relancer",
                    "desinscription", "absence_bureau", "autre",
                ],
                "description": (
                    "interesse: veut avancer / en savoir plus. pas_interesse: refus "
                    "clair. a_relancer: demande de recontacter plus tard. "
                    "desinscription: demande explicite d'arrêt de contact. "
                    "absence_bureau: réponse automatique (vacances, hors bureau). "
                    "autre: ne rentre dans aucune case ci-dessus."
                ),
            },
            "raison": {"type": "string", "description": "justification courte de la catégorie choisie"},
            "action_recommandee": {
                "type": "string",
                "description": "ce qu'un humain devrait faire ensuite, en une phrase",
            },
        },
        "required": ["categorie", "raison", "action_recommandee"],
    },
}

# Catégories qui déclenchent une mise à jour automatique du statut.
# a_relancer / absence_bureau / autre sont volontairement absentes : un
# humain doit trancher, l'agent ne fait que remonter l'info.
CATEGORIE_VERS_STATUT = {
    "interesse": "repondu",
    "pas_interesse": "perdu",
    "desinscription": "desinscrit",
}


def build_prompt(prospect: dict, email: dict) -> str:
    return f"""Tu es un agent qui classe les réponses email reçues dans le
cadre d'une prospection commerciale B2B. Appelle l'outil `classifier_email`
avec ta décision, ne réponds jamais en texte libre.

# Prospect
{prospect.get('prenom', '')} {prospect.get('nom', '')} - {prospect.get('poste', '')} chez {prospect.get('entreprise', '')}

# Email reçu
De : {email.get('de', '')}
Sujet : {email.get('sujet', '')}
Contenu :
{email.get('corps', '')[:3000]}

Si le message contient la moindre demande d'arrêt de contact (désinscription,
"ne me contactez plus", "retirez-moi de votre liste", etc.), classe-le
TOUJOURS en 'desinscription', même si le reste du message est ambigu :
mieux vaut arrêter de contacter quelqu'un par excès de prudence que l'inverse."""


def classify_email(prospect: dict, email: dict, client=None) -> dict:
    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        tools=[TOOL_CLASSIFICATION],
        tool_choice={"type": "tool", "name": "classifier_email"},
        messages=[{"role": "user", "content": build_prompt(prospect, email)}],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "classifier_email":
            return block.input
    raise RuntimeError("L'API n'a pas retourné d'appel d'outil (réponse inattendue).")


def _fake_classification() -> dict:
    return {
        "categorie": "interesse",
        "raison": "[DRY-RUN] Réponse simulée, aucun appel API réel n'a été fait.",
        "action_recommandee": "[DRY-RUN] aucune action réelle recommandée.",
    }


def _extraire_adresse(champ_de: str) -> str:
    """'Jean Dupont <jean@acme.com>' -> 'jean@acme.com' (minuscules)."""
    m = re.search(r"<([^>]+)>", champ_de)
    adresse = m.group(1) if m else champ_de
    return adresse.strip().lower()


def _lots(elements: list, taille: int):
    for i in range(0, len(elements), taille):
        yield elements[i:i + taille]


def collecter_nouveaux_emails(service, prospects: list[dict]) -> list[tuple]:
    """Retourne [(prospect, email_dict)] pour tous les nouveaux messages reçus
    des prospects, via des requêtes Gmail groupées par lots.

    Deux garde-fous essentiels :
    - un message dont l'expéditeur est NOTRE PROPRE adresse d'envoi n'est
      jamais une réponse de prospect (ça arrive en s'auto-testant avec la
      même adresse pour l'envoi et la réception, ou simplement si un
      prospect partage son adresse avec la nôtre par erreur de saisie) —
      sans ce filtre, une requête `from:(...)` peut retomber sur tout
      l'historique déjà envoyé depuis ce compte et le faire classer comme
      autant de "réponses", ce qui gaspille des appels API pour rien.
    - si plusieurs prospects partagent la même adresse email, on les
      regroupe au lieu d'en écraser silencieusement un par l'autre : un
      dict simple {email: prospect} ne garde que le dernier inséré."""
    mon_adresse = gmail_client.get_my_email_address(service)

    par_adresse: dict[str, list[dict]] = {}
    for p in prospects:
        email = (p.get("email") or "").strip().lower()
        if email:
            par_adresse.setdefault(email, []).append(p)

    resultats = []
    for lot in _lots(list(par_adresse.keys()), TAILLE_LOT_GMAIL):
        query = "from:(" + " OR ".join(lot) + ")"
        for m in gmail_client.search_messages(service, query=query, max_results=50):
            if db.est_email_traite(m["id"]):
                continue
            contenu = gmail_client.get_message_content(service, m["id"])
            expediteur = _extraire_adresse(contenu.get("de", ""))

            if mon_adresse and expediteur == mon_adresse:
                # Notre propre email (envoyé par nous, ou reçu en copie sur
                # le même compte) — jamais une réponse. On le marque traité
                # pour ne pas le re-scanner à chaque clic, sans jamais
                # l'envoyer à Claude pour classification.
                db.marquer_email_traite(m["id"], None)
                continue

            for prospect in par_adresse.get(expediteur, []):
                resultats.append((prospect, contenu))
    return resultats


def traiter_email(prospect: dict, email: dict, dry_run: bool = False, client=None) -> dict:
    """Classe UN email et écrit le résultat en base. Brique utilisée par
    run() (CLI) et par le job du dashboard."""
    resultat = _fake_classification() if dry_run else classify_email(prospect, email, client)

    nouveau_statut = CATEGORIE_VERS_STATUT.get(resultat["categorie"])
    if nouveau_statut:
        db.update_statut(prospect["id"], nouveau_statut)

    db.add_interaction(
        prospect["id"], "email_recu",
        f"[{resultat['categorie']}] {resultat['raison']} -> {resultat['action_recommandee']}",
    )
    if not dry_run:
        db.marquer_email_traite(email["id"], prospect["id"])
    return resultat


def _fake_email(prospect: dict) -> dict:
    return {
        "id": f"dry-run-{prospect['id']}",
        "thread_id": "dry-run",
        "de": prospect.get("email") or "test@example.com",
        "sujet": "Re: prise de contact",
        "date": "dry-run",
        "corps": "Ceci est un email simulé pour tester le flux sans Gmail ni API Claude.",
    }


def run(dry_run: bool = False, test_connexion: bool = False) -> None:
    if test_connexion:
        service = gmail_client.get_service()
        messages = gmail_client.search_messages(service, query="", max_results=3)
        print(f"Connexion Gmail OK. {len(messages)} message(s) récent(s) trouvé(s) dans la boîte.")
        return

    prospects = db.list_prospects_avec_email()
    if not prospects:
        print("Aucun prospect avec une adresse email en base.")
        return

    if dry_run:
        paires = [(p, _fake_email(p)) for p in prospects]
    else:
        service = gmail_client.get_service()
        paires = collecter_nouveaux_emails(service, prospects)

    total_traites = 0
    for prospect, email in paires:
        try:
            resultat = traiter_email(prospect, email, dry_run=dry_run)
            total_traites += 1
            marqueur = "🔴" if resultat["categorie"] == "desinscription" else "  "
            print(f"{marqueur}[{prospect['id']}] {prospect.get('prenom','')} {prospect.get('nom','')} "
                  f"- {resultat['categorie']} : {resultat['raison']}")
        except Exception as exc:  # noqa: BLE001 - continuer sur les autres emails
            print(f"  [{prospect['id']}] ERREUR sur email {email.get('id')} : {exc}", file=sys.stderr)

    print(f"\n{total_traites} email(s) classé(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent email (lecture seule)")
    parser.add_argument("--dry-run", action="store_true", help="simule sans toucher Gmail ni l'API Claude")
    parser.add_argument("--test-connexion", action="store_true", help="vérifie juste la connexion OAuth Gmail")
    args = parser.parse_args()

    if not args.dry_run and not args.test_connexion and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY n'est pas définie. Utilise --dry-run pour tester sans clé.", file=sys.stderr)
        sys.exit(1)

    db.init_db()
    try:
        run(dry_run=args.dry_run, test_connexion=args.test_connexion)
    except FileNotFoundError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)
