"""
Agent de qualification.

Rôle : lire les prospects au statut 'nouveau' du profil actif, les comparer
à l'ICP du profil (config/profils/<profil>/icp.yaml), et écrire un score +
une décision qualifié/non-qualifié dans la base.

Usage :
    python3 -m agents.qualification                 # qualifie tous les nouveaux prospects du profil actif
    python3 -m agents.qualification --dry-run        # teste le flux sans appeler l'API (réponse simulée)
    python3 -m agents.qualification --prospect-id 3  # qualifie un seul prospect
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Chargé ici, AVANT de lire CLAUDE_MODEL ci-dessous : ce module peut être
# importé (par le dashboard, par exemple) sans jamais passer par le bloc
# `if __name__ == "__main__"` plus bas, donc c'est le seul endroit qui
# garantit que la surcharge posée dans .env est bien prise en compte.
load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))
import profils  # noqa: E402
from db import database as db  # noqa: E402

# Identifiant de modèle de l'API Anthropic. Surchargeable via .env
# (CLAUDE_MODEL=...) sans toucher au code.
# Modèle "rapide" : qualifier un prospect (comparer à une grille de critères,
# sortir un score) est une tâche mécanique de classification, pas de la
# rédaction — Haiku fait aussi bien pour ce genre de tâche que Sonnet, à
# environ la moitié du prix. Surchargeable via .env (CLAUDE_MODEL_RAPIDE=...).
# Modèle "rapide" : qualifier un prospect (comparer à une grille de critères,
# sortir un score) est une tâche mécanique de classification, pas de la
# rédaction — Haiku fait aussi bien pour ce genre de tâche que Sonnet, à
# environ la moitié du prix. Lu à CHAQUE appel (pas mis en cache au chargement
# du module) : un changement fait depuis Paramètres → Clé & modèles prend
# effet tout de suite, sans redémarrer l'app. Surchargeable via .env
# (CLAUDE_MODEL_RAPIDE=...).
def _modele() -> str:
    return os.environ.get("CLAUDE_MODEL_RAPIDE", "claude-haiku-4-5-20251001")

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


def build_system(icp: dict) -> list[dict]:
    """Partie du prompt IDENTIQUE pour tous les prospects d'un même profil
    (produit, ICP, exclusions, seuil) — posée en 'system' avec un point de
    cache. Sur un lot de N qualifications, seul le 1er appel paie le plein
    tarif pour ce bloc ; les N-1 suivants le lisent en cache à ~10% du prix
    (tant qu'ils s'enchaînent dans les 5 minutes). Sous le seuil minimum de
    mise en cache d'Anthropic (1024 tokens pour Sonnet), le cache est
    simplement ignoré sans erreur — aucun risque à le marquer toujours."""
    produit = icp.get("produit", {})
    cible = icp.get("cible", {})
    texte = f"""Tu es un agent de qualification de prospects B2B. Compare le
prospect fourni à l'ICP (Ideal Customer Profile) ci-dessous et appelle
l'outil `qualifier_prospect` avec ta décision. Ne réponds jamais en texte
libre, utilise uniquement l'outil.

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

Seuil de qualification configuré : {icp.get('seuil_qualification', 60)}/100.
Sois honnête sur le score : un profil moyen doit avoir un score moyen, ne
gonfle pas artificiellement les scores."""
    return [{"type": "text", "text": texte, "cache_control": {"type": "ephemeral"}}]


def build_prompt(prospect: dict) -> str:
    """Partie du prompt PROPRE à ce prospect — jamais mise en cache, change
    à chaque appel."""
    return f"""# Prospect à qualifier
Prénom : {prospect.get('prenom', '')}
Nom : {prospect.get('nom', '')}
Poste : {prospect.get('poste', '')}
Entreprise : {prospect.get('entreprise', '')}
Secteur : {prospect.get('secteur', '')}
Taille d'entreprise : {prospect.get('taille_entreprise', '')}
Notes additionnelles : {prospect.get('notes', '')}"""


def _usage_de(response) -> dict:
    """Extrait tokens_entree/tokens_sortie/recherches_web d'une réponse API,
    quel que soit l'agent qui a fait l'appel — même format partout.
    tokens_entree inclut les tokens lus depuis le cache (cache_read) et ceux
    qui viennent d'y être écrits (cache_creation) : ce sont de vrais tokens
    traités, juste facturés très différemment (~10% du prix en lecture,
    ~125% en écriture) — les compter donne un total exact, pas un total qui
    semblerait chuter artificiellement dès que le cache s'active."""
    usage = getattr(response, "usage", None)
    if not usage:
        return {"tokens_entree": 0, "tokens_sortie": 0, "recherches_web": 0}
    server_tool_use = getattr(usage, "server_tool_use", None)
    recherches = getattr(server_tool_use, "web_search_requests", 0) if server_tool_use else 0
    tokens_entree = (
        (getattr(usage, "input_tokens", 0) or 0)
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        + (getattr(usage, "cache_read_input_tokens", 0) or 0)
    )
    return {
        "tokens_entree": tokens_entree,
        "tokens_sortie": getattr(usage, "output_tokens", 0) or 0,
        "recherches_web": recherches or 0,
    }


def qualify_prospect(prospect: dict, icp: dict, client=None) -> tuple[dict, dict]:
    """Appelle Claude et retourne (résultat structuré, usage tokens)."""
    if client is None:
        import anthropic

        client = anthropic.Anthropic()  # lit ANTHROPIC_API_KEY dans l'environnement

    response = client.messages.create(
        model=_modele(),
        max_tokens=1024,
        tools=[TOOL_QUALIFICATION],
        tool_choice={"type": "tool", "name": "qualifier_prospect"},
        system=build_system(icp),
        messages=[{"role": "user", "content": build_prompt(prospect)}],
    )
    usage = _usage_de(response)

    for block in response.content:
        if block.type == "tool_use" and block.name == "qualifier_prospect":
            return block.input, usage

    raise RuntimeError("L'API n'a pas retourné d'appel d'outil (réponse inattendue).")


def _fake_qualification(prospect: dict, icp: dict) -> tuple[dict, dict]:
    """Réponse simulée pour --dry-run : teste toute la chaîne sans clé API."""
    return {
        "qualifie": True,
        "score": 75,
        "raison": "[DRY-RUN] Réponse simulée, aucun appel API réel n'a été fait.",
        "signaux_positifs": ["exécution en mode test"],
        "signaux_negatifs": [],
    }, {"tokens_entree": 0, "tokens_sortie": 0, "recherches_web": 0}


def qualifier_un(prospect: dict, icp: dict, dry_run: bool = False, client=None) -> dict:
    """Qualifie UN prospect et écrit le résultat en base. Retourne le résultat.
    Brique de base utilisée par run() (CLI) et par le job du dashboard.

    Le booléen 'qualifie' que Claude retourne n'est qu'indicatif : c'est le
    score comparé au seuil configuré dans l'ICP (Paramètres) qui décide
    vraiment, pour que ce réglage ait un effet mécanique garanti plutôt que
    d'être une simple suggestion glissée dans le prompt. Sans ça, deux
    prospects au même score pourraient finir dans des statuts différents
    selon l'humeur du modèle, et changer le seuil dans Paramètres n'aurait
    aucun effet réel."""
    resultat, usage = _fake_qualification(prospect, icp) if dry_run else qualify_prospect(prospect, icp, client)
    try:
        seuil = int(icp.get("seuil_qualification", 60))
    except (TypeError, ValueError):
        seuil = 60
    decision_seuil = resultat["score"] >= seuil
    if decision_seuil != resultat["qualifie"]:
        resultat["raison"] = (
            f"{resultat['raison']} (seuil à {seuil} appliqué : "
            f"{'qualifié' if decision_seuil else 'non qualifié'} au score {resultat['score']}.)"
        )
    resultat["qualifie"] = decision_seuil
    db.update_qualification(
        prospect_id=prospect["id"],
        qualifie=resultat["qualifie"],
        score=resultat["score"],
        raison=resultat["raison"],
        signaux_positifs=resultat["signaux_positifs"],
        signaux_negatifs=resultat["signaux_negatifs"],
        tokens_entree=usage["tokens_entree"],
        tokens_sortie=usage["tokens_sortie"],
    )
    return resultat


def run(prospect_id: int | None = None, dry_run: bool = False, profil: str | None = None) -> None:
    profil = profil or db.profil_actif()
    icp = profils.load_icp(profil)
    if icp.get("produit", {}).get("nom") == "À DÉFINIR":
        print(
            f"⚠️  L'ICP du profil '{profil}' n'a pas encore été rempli.\n"
            "   Les qualifications ne voudront rien dire tant que ce n'est pas fait.\n",
            file=sys.stderr,
        )

    if prospect_id is not None:
        prospects = [p for p in [db.get_prospect(prospect_id)] if p]
    else:
        prospects = db.list_prospects(statut="nouveau", profil=profil)

    if not prospects:
        print(f"Aucun prospect à qualifier (statut 'nouveau', profil '{profil}').")
        return

    print(f"{len(prospects)} prospect(s) à qualifier (profil '{profil}')...")
    for p in prospects:
        try:
            resultat = qualifier_un(p, icp, dry_run=dry_run)
            statut = "✅ QUALIFIÉ" if resultat["qualifie"] else "❌ non qualifié"
            print(f"  [{p['id']}] {p.get('prenom', '')} {p.get('nom', '')} "
                  f"({p.get('entreprise', '')}) -> {statut} (score {resultat['score']})")
        except Exception as exc:  # noqa: BLE001 - on veut continuer sur les autres prospects
            print(f"  [{p['id']}] ERREUR : {exc}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent de qualification de prospects")
    parser.add_argument("--dry-run", action="store_true", help="simule sans appeler l'API")
    parser.add_argument("--prospect-id", type=int, default=None, help="ne qualifier qu'un seul prospect")
    parser.add_argument("--profil", type=str, default=None, help="profil à utiliser (défaut : profil actif)")
    args = parser.parse_args()

    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY n'est pas définie (vérifie ton .env). "
              "Utilise --dry-run pour tester sans clé.", file=sys.stderr)
        sys.exit(1)

    db.init_db()
    run(prospect_id=args.prospect_id, dry_run=args.dry_run, profil=args.profil)
