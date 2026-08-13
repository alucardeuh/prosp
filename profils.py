"""
Profils de prospection — un profil = un ICP + un brief d'email + ses prospects.

Le projet sert deux activités différentes (SAMMPO aujourd'hui, un projet
médical ensuite) : ce qu'on vend et à qui on le vend change, mais toute la
mécanique (qualification, envoi, relances, lecture) est identique. Plutôt
que deux copies du code, chaque profil vit dans son dossier :

    config/profils/sammpo/icp.yaml + email_brief.yaml
    config/profils/medical/icp.yaml + email_brief.yaml

Le profil actif est stocké en base (reglages.profil_actif) et se change
depuis l'interface. Chaque prospect porte le profil dans lequel il a été
créé et n'apparaît que quand ce profil est actif.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).parent / "config"
PROFILS_DIR = CONFIG_DIR / "profils"

ICP_VIERGE = {
    "produit": {
        "nom": "À DÉFINIR",
        "description": "Décris ici ce que tu vends.",
        "proposition_de_valeur": "Décris ici la valeur concrète pour le client.",
    },
    "cible": {
        "secteurs": [], "taille_entreprise": [], "postes": [],
        "zones_geographiques": ["France"], "signaux_achat": [],
    },
    "exclusions": [],
    "seuil_qualification": 60,
}

BRIEF_VIERGE = {
    "ton": "Professionnel mais direct, phrases courtes, pas de jargon.",
    "longueur_max_mots": 150,
    "structure_attendue": (
        "Une accroche personnalisée basée sur un élément réel du profil, "
        "puis la proposition de valeur adaptée, puis un call-to-action clair "
        "(échange de 15-20 minutes)."
    ),
    "signature": "Cordialement,",
    "mention_obligatoire": (
        "Pour ne plus recevoir de message de notre part, répondez \"STOP\" "
        "à cet email."
    ),
}


def _migrer_anciens_fichiers() -> None:
    """Déplace l'ancien config/icp.yaml + email_brief.yaml (racine de config/)
    vers config/profils/sammpo/ au premier lancement de la nouvelle version.
    Idempotent : ne fait rien si la migration a déjà eu lieu."""
    dossier_sammpo = PROFILS_DIR / "sammpo"
    ancien_icp = CONFIG_DIR / "icp.yaml"
    ancien_brief = CONFIG_DIR / "email_brief.yaml"

    if (dossier_sammpo / "icp.yaml").exists():
        return
    dossier_sammpo.mkdir(parents=True, exist_ok=True)
    if ancien_icp.exists():
        shutil.move(str(ancien_icp), str(dossier_sammpo / "icp.yaml"))
        print("Migration : config/icp.yaml -> config/profils/sammpo/icp.yaml", file=sys.stderr)
    else:
        _ecrire_yaml(dossier_sammpo / "icp.yaml", ICP_VIERGE)
    if ancien_brief.exists():
        shutil.move(str(ancien_brief), str(dossier_sammpo / "email_brief.yaml"))
        print("Migration : config/email_brief.yaml -> config/profils/sammpo/email_brief.yaml", file=sys.stderr)
    else:
        _ecrire_yaml(dossier_sammpo / "email_brief.yaml", BRIEF_VIERGE)


def _ecrire_yaml(chemin: Path, contenu: dict) -> None:
    with open(chemin, "w", encoding="utf-8") as f:
        yaml.safe_dump(contenu, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def init_profils() -> None:
    """À appeler au démarrage : migre les anciens fichiers et garantit
    qu'au moins le profil sammpo existe."""
    PROFILS_DIR.mkdir(parents=True, exist_ok=True)
    _migrer_anciens_fichiers()


def list_profils() -> list[str]:
    init_profils()
    return sorted(
        d.name for d in PROFILS_DIR.iterdir()
        if d.is_dir() and (d / "icp.yaml").exists()
    )


def creer_profil(nom: str) -> str:
    """Crée un nouveau profil vierge. Retourne l'identifiant normalisé."""
    identifiant = re.sub(r"[^a-z0-9_-]", "", nom.strip().lower().replace(" ", "-"))
    if not identifiant:
        raise ValueError("Nom de profil invalide.")
    dossier = PROFILS_DIR / identifiant
    if (dossier / "icp.yaml").exists():
        raise ValueError(f"Le profil '{identifiant}' existe déjà.")
    dossier.mkdir(parents=True, exist_ok=True)
    _ecrire_yaml(dossier / "icp.yaml", ICP_VIERGE)
    _ecrire_yaml(dossier / "email_brief.yaml", BRIEF_VIERGE)
    return identifiant


def chemin_icp(profil: str) -> Path:
    return PROFILS_DIR / profil / "icp.yaml"


def chemin_brief(profil: str) -> Path:
    return PROFILS_DIR / profil / "email_brief.yaml"


def load_icp(profil: str) -> dict:
    init_profils()
    with open(chemin_icp(profil), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_brief(profil: str) -> dict:
    init_profils()
    with open(chemin_brief(profil), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_icp(profil: str, icp: dict) -> None:
    _ecrire_yaml(chemin_icp(profil), icp)


def save_brief(profil: str, brief: dict) -> None:
    _ecrire_yaml(chemin_brief(profil), brief)
