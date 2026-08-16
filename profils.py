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
import unicodedata
from pathlib import Path

import yaml

from db.database import CHAMPS_PROSPECT

# Identifiants déjà utilisés ailleurs dans le schéma prospects — un champ
# personnalisé du même nom ferait dérouter silencieusement les données au
# mauvais endroit (ex : un champ "poste" ferait perdre le vrai poste du
# prospect, sa valeur atterrissant dans champs_perso au lieu de la colonne
# réelle). CHAMPS_PROSPECT + les colonnes techniques non éditables.
NOMS_RESERVES = CHAMPS_PROSPECT | {
    "id", "statut", "score_qualification", "raison_qualification",
    "signaux_positifs", "signaux_negatifs", "nb_relances", "champs_perso",
    "date_creation", "date_derniere_action", "getsales_lead_uuid",
}

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
    identifiant = _identifiant_sur(nom)
    if not identifiant:
        raise ValueError("Nom de profil invalide.")
    dossier = PROFILS_DIR / identifiant
    if (dossier / "icp.yaml").exists():
        raise ValueError(f"Le profil '{identifiant}' existe déjà.")
    dossier.mkdir(parents=True, exist_ok=True)
    _ecrire_yaml(dossier / "icp.yaml", ICP_VIERGE)
    _ecrire_yaml(dossier / "email_brief.yaml", BRIEF_VIERGE)
    _ecrire_yaml(dossier / "champs.yaml", {"champs": []})
    _ecrire_yaml(dossier / "modeles.yaml", {"modeles": []})
    return identifiant


def chemin_icp(profil: str) -> Path:
    return PROFILS_DIR / profil / "icp.yaml"


def chemin_brief(profil: str) -> Path:
    return PROFILS_DIR / profil / "email_brief.yaml"


def chemin_champs(profil: str) -> Path:
    return PROFILS_DIR / profil / "champs.yaml"


def chemin_modeles(profil: str) -> Path:
    return PROFILS_DIR / profil / "modeles.yaml"


EXTENSIONS_SIGNATURE = ("png", "jpg", "jpeg", "gif")


def chemin_signature(profil: str) -> Path | None:
    """Chemin de l'image de signature du profil, si elle existe — n'importe
    quelle extension d'image courante, l'extension d'origine est conservée
    (déterminant le bon type MIME à l'envoi)."""
    dossier = PROFILS_DIR / profil
    for ext in EXTENSIONS_SIGNATURE:
        chemin = dossier / f"signature.{ext}"
        if chemin.exists():
            return chemin
    return None


def supprimer_signature(profil: str) -> None:
    chemin = chemin_signature(profil)
    if chemin:
        chemin.unlink()


def sauver_signature(profil: str, donnees: bytes, extension: str) -> None:
    """Remplace la signature du profil. Supprime d'abord l'ancienne (son
    extension a pu être différente d'un upload à l'autre — sinon les deux
    fichiers coexisteraient et chemin_signature() prendrait toujours le
    même par ordre de recherche, laissant l'autre orphelin sur le disque)."""
    extension = extension.lower().lstrip(".")
    if extension not in EXTENSIONS_SIGNATURE:
        raise ValueError(f"Format d'image non supporté : .{extension} "
                         f"(formats acceptés : {', '.join(EXTENSIONS_SIGNATURE)}).")
    supprimer_signature(profil)
    dossier = PROFILS_DIR / profil
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / f"signature.{extension}").write_bytes(donnees)


def load_icp(profil: str) -> dict:
    init_profils()
    with open(chemin_icp(profil), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_brief(profil: str) -> dict:
    init_profils()
    with open(chemin_brief(profil), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_champs(profil: str) -> list[dict]:
    """Champs personnalisés définis pour ce profil : [{nom, libelle}, ...].
    'nom' est l'identifiant technique (clé JSON), 'libelle' ce qui s'affiche
    dans l'interface. Fichier créé vide au besoin (profils migrés avant
    l'existence de cette fonctionnalité n'en ont pas encore)."""
    chemin = chemin_champs(profil)
    if not chemin.exists():
        _ecrire_yaml(chemin, {"champs": []})
    with open(chemin, encoding="utf-8") as f:
        contenu = yaml.safe_load(f) or {}
    return contenu.get("champs", [])


def save_champs(profil: str, champs: list[dict]) -> None:
    _ecrire_yaml(chemin_champs(profil), {"champs": champs})


def load_modeles(profil: str) -> list[dict]:
    """Emails-types de ce profil : [{titre, objet, corps}, ...] — utilisés
    comme exemples concrets ('few-shot') dans le prompt de rédaction, pour
    que Claude s'inspire d'un ton et d'une structure qui ont déjà fait leurs
    preuves plutôt que de partir uniquement d'instructions abstraites.
    Fichier créé vide au besoin (profils créés avant cette fonctionnalité)."""
    chemin = chemin_modeles(profil)
    if not chemin.exists():
        _ecrire_yaml(chemin, {"modeles": []})
    with open(chemin, encoding="utf-8") as f:
        contenu = yaml.safe_load(f) or {}
    return contenu.get("modeles", [])


def save_modeles(profil: str, modeles: list[dict]) -> None:
    _ecrire_yaml(chemin_modeles(profil), {"modeles": modeles})


def ajouter_modele(profil: str, titre: str, objet: str, corps: str) -> None:
    if not titre.strip() or not corps.strip():
        raise ValueError("Le titre et le corps du modèle sont obligatoires.")
    modeles = load_modeles(profil)
    modeles.append({"titre": titre.strip(), "objet": objet.strip(), "corps": corps.strip()})
    save_modeles(profil, modeles)


def supprimer_modele(profil: str, index: int) -> None:
    modeles = load_modeles(profil)
    if 0 <= index < len(modeles):
        modeles.pop(index)
        save_modeles(profil, modeles)


def _identifiant_sur(texte: str) -> str:
    """Transforme un libellé libre en identifiant technique sûr : translittère
    les accents d'abord (sinon 'estimé' -> 'estim_' -> 'estim', perdant sa
    dernière lettre au strip), puis ne garde que [a-z0-9_]."""
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9_]", "_", sans_accents.strip().lower().replace(" ", "_")).strip("_")


def ajouter_champ(profil: str, nom: str, libelle: str) -> None:
    """Ajoute une variable personnalisée. 'nom' est normalisé en identifiant
    technique sûr (utilisé comme clé JSON et comme name= de formulaire)."""
    identifiant = _identifiant_sur(nom)
    if not identifiant:
        raise ValueError("Nom de champ invalide.")
    if identifiant in NOMS_RESERVES:
        raise ValueError(
            f"'{identifiant}' est déjà un champ existant (prénom, nom, poste...) — "
            "choisis un autre nom pour éviter que les deux se marchent dessus."
        )
    champs = load_champs(profil)
    if any(c["nom"] == identifiant for c in champs):
        raise ValueError(f"Le champ '{identifiant}' existe déjà.")
    champs.append({"nom": identifiant, "libelle": libelle.strip() or identifiant})
    save_champs(profil, champs)


def supprimer_champ(profil: str, nom: str) -> None:
    """Retire la DÉFINITION du champ — les valeurs déjà enregistrées sur les
    prospects existants restent en base (orphelines, invisibles) plutôt que
    d'aller les effacer une par une ; recréer un champ du même nom les
    ferait réapparaître, mais ce n'est pas la garantie recherchée ici."""
    champs = [c for c in load_champs(profil) if c["nom"] != nom]
    save_champs(profil, champs)


def save_icp(profil: str, icp: dict) -> None:
    _ecrire_yaml(chemin_icp(profil), icp)


def save_brief(profil: str, brief: dict) -> None:
    _ecrire_yaml(chemin_brief(profil), brief)
