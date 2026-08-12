"""
Agent de qualification.

Rôle : lire les prospects au statut 'nouveau' dans la base, les comparer à
l'ICP défini dans config/icp.yaml, et écrire un score + une décision
qualifié/non-qualifié dans la base. Zéro appel externe risqué (pas de
LinkedIn, pas d'email) : c'est pour ça que c'est le premier agent à
construire.

Usage :
    python -m agents.qualification                 # qualifie tous les nouveaux prospects
    python -m agents.qualification --dry-run        # teste le flux sans appeler l'API (réponse simulée)
    python -m agents.qualification --prospect-id 3  # qualifie un seul prospect
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

ICP_PATH = Path(__file__).parent.parent / "config" / "icp.yaml"
MODEL = "claude-sonnet-5"

TOOL_QUALIFICATION = {
    "name": "qualifier_prospect",
    "description": "Enregistre la qualification d'un prospect au regard de l'ICP fourni.",
    "input_schema": {
        "type": "object",
        "properties": {
            "qualifie": {
                "type": "boolean",
                "description": "true si le prospect correspond à l'ICP et mérite d'être contacté",
            },
            "score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "score de correspondance à l'ICP, 0 = aucun rapport, 100 = client idéal",
            },
            "raison": {
                "type": "string",
                "description": "justification courte (2-3 phrases) de la décision",
            },
            "signaux_positifs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "éléments concrets du profil qui matchent l'ICP",
            },
            "signaux_negatifs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "éléments qui posent question ou disqualifient",
            },
        },
        "required": ["qualifie", "score", "raison", "signaux_positifs", "signaux_negatifs"],
    },
}


def load_icp(path: Path = ICP_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_prompt(prospect: dict, icp: dict) -> str:
    produit = icp.get("produit", {})
    cible = icp.get("cible", {})
    return f"""Tu es un agent de qualification de prospects B2B. Compare le
prospect ci-dessous à l'ICP (Ideal Customer Profile) et appelle l'outil
`qualifier_prospect` avec ta décision. Ne réponds jamais en texte libre,
utilise uniquement l'outil.

# Produit / service vendu
Nom : {produit.get('nom', 'N/A')}
Description : {produit.get('description', 'N/A')}
Proposition de valeur : {produit.get('proposition_de_valeur', 'N/A')}

# ICP recherché
Secteurs : {', '.join(cible.get('secteurs', [])) or 'non précisé'}
Taille d'entreprise : {', '.join(cible.get('taille_entreprise', [])) or 'non précisé'}
Postes cibles : {', '.join(cible.get('postes', [])) or 'non précisé'}
Zones géographiques : {', '.join(cible.get('zones_geographiques', [])) or 'non précisé'}
Signaux d'achat recherchés : {', '.join(cible.get('signaux_achat', [])) or 'non précisé'}

# Exclusions
{chr(10).join('- ' + e for e in icp.get('exclusions', [])) or 'aucune'}

# Prospect à qualifier
Prénom : {prospect.get('prenom', '')}
Nom : {prospect.get('nom', '')}
Poste : {prospect.get('poste', '')}
Entreprise : {prospect.get('entreprise', '')}
Secteur : {prospect.get('secteur', '')}
Taille d'entreprise : {prospect.get('taille_entreprise', '')}
Notes additionnelles : {prospect.get('notes', '')}

Seuil de qualification configuré : {icp.get('seuil_qualification', 60)}/100.
Sois honnête sur le score : un profil moyen doit avoir un score moyen, ne
gonfle pas artificiellement les scores."""


def qualify_prospect(prospect: dict, icp: dict, client=None) -> dict:
    """Appelle Claude et retourne le dict structuré de qualification."""
    if client is None:
        import anthropic

        client = anthropic.Anthropic()  # lit ANTHROPIC_API_KEY dans l'environnement

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        tools=[TOOL_QUALIFICATION],
        tool_choice={"type": "tool", "name": "qualifier_prospect"},
        messages=[{"role": "user", "content": build_prompt(prospect, icp)}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "qualifier_prospect":
            return block.input

    raise RuntimeError("L'API n'a pas retourné d'appel d'outil (réponse inattendue).")


def _fake_qualification(prospect: dict, icp: dict) -> dict:
    """Réponse simulée pour --dry-run : permet de tester toute la chaîne
    (lecture DB -> prompt -> écriture DB) sans clé API ni coût."""
    return {
        "qualifie": True,
        "score": 75,
        "raison": "[DRY-RUN] Réponse simulée, aucun appel API réel n'a été fait.",
        "signaux_positifs": ["exécution en mode test"],
        "signaux_negatifs": [],
    }


def run(prospect_id: int | None = None, dry_run: bool = False) -> None:
    icp = load_icp()
    if icp.get("produit", {}).get("nom") == "À DÉFINIR":
        print(
            "⚠️  config/icp.yaml n'a pas encore été rempli avec ton produit réel.\n"
            "   Les qualifications ne voudront rien dire tant que ce fichier "
            "n'est pas personnalisé.\n",
            file=sys.stderr,
        )

    if prospect_id is not None:
        prospects = [p for p in [db.get_prospect(prospect_id)] if p]
    else:
        prospects = db.list_prospects(statut="nouveau")

    if not prospects:
        print("Aucun prospect à qualifier (statut 'nouveau').")
        return

    print(f"{len(prospects)} prospect(s) à qualifier...")
    for p in prospects:
        try:
            if dry_run:
                resultat = _fake_qualification(p, icp)
            else:
                resultat = qualify_prospect(p, icp)

            db.update_qualification(
                prospect_id=p["id"],
                qualifie=resultat["qualifie"],
                score=resultat["score"],
                raison=resultat["raison"],
                signaux_positifs=resultat["signaux_positifs"],
                signaux_negatifs=resultat["signaux_negatifs"],
            )
            statut = "✅ QUALIFIÉ" if resultat["qualifie"] else "❌ non qualifié"
            print(f"  [{p['id']}] {p.get('prenom', '')} {p.get('nom', '')} "
                  f"({p.get('entreprise', '')}) -> {statut} (score {resultat['score']})")
        except Exception as exc:  # noqa: BLE001 - on veut continuer sur les autres prospects
            print(f"  [{p['id']}] ERREUR : {exc}", file=sys.stderr)


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="Agent de qualification de prospects")
    parser.add_argument("--dry-run", action="store_true", help="simule sans appeler l'API")
    parser.add_argument("--prospect-id", type=int, default=None, help="ne qualifier qu'un seul prospect")
    args = parser.parse_args()

    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY n'est pas définie (vérifie ton .env). "
              "Utilise --dry-run pour tester sans clé.", file=sys.stderr)
        sys.exit(1)

    db.init_db()
    run(prospect_id=args.prospect_id, dry_run=args.dry_run)
