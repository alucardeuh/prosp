"""
Client GetSales.io — API LinkedIn.

Documentation officielle : https://api.getsales.io
Auth : header Authorization: Bearer {TOKEN}

⚠️ Le host (ex: amazing.getsales.io dans la doc officielle) est spécifique
à ton compte. Trouve le tien depuis ton compte GetSales (page "API Keys")
et mets-le dans .env sous GETSALES_HOST — sans ça, rien ne fonctionnera.

Trois identifiants à récupérer dans ton dashboard GetSales avant de
pouvoir utiliser ce module :
    - GETSALES_HOST              : ton sous-domaine (page API Keys)
    - GETSALES_API_KEY           : ton token (page API Keys)
    - GETSALES_LIST_UUID         : page "Lists" > 3 points > "Copy List ID"
    - GETSALES_SENDER_PROFILE_UUID : page "Sender Profiles"
"""
from __future__ import annotations

import os

import requests

HOST = os.environ.get("GETSALES_HOST", "")
API_KEY = os.environ.get("GETSALES_API_KEY", "")
LIST_UUID = os.environ.get("GETSALES_LIST_UUID", "")
SENDER_PROFILE_UUID = os.environ.get("GETSALES_SENDER_PROFILE_UUID", "")


def _base_url() -> str:
    if not HOST:
        raise RuntimeError(
            "GETSALES_HOST n'est pas défini dans .env. Trouve-le sur la page "
            "'API Keys' de ton compte GetSales.io."
        )
    return HOST if HOST.startswith("http") else f"https://{HOST}"


def _headers() -> dict:
    if not API_KEY:
        raise RuntimeError("GETSALES_API_KEY n'est pas défini dans .env.")
    return {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


def _request(method: str, path: str, **kwargs) -> dict | list:
    url = f"{_base_url()}{path}"
    response = requests.request(method, url, headers=_headers(), timeout=30, **kwargs)
    if not response.ok:
        raise RuntimeError(
            f"GetSales {method} {path} -> {response.status_code}: {response.text[:500]}"
        )
    return response.json() if response.text else {}


def extract_linkedin_identifier(linkedin_url: str) -> str:
    """'https://www.linkedin.com/in/jean-dupont-123/' -> 'jean-dupont-123'"""
    cleaned = linkedin_url.rstrip("/")
    return cleaned.split("/in/")[-1].split("?")[0]


def upsert_lead(prospect: dict, note_connexion: str | None = None, premier_message: str | None = None) -> dict:
    """Ajoute (ou met à jour) un prospect comme lead dans GetSales.
    Retourne le lead créé — son `uuid` est nécessaire pour envoyer un message."""
    if not LIST_UUID:
        raise RuntimeError(
            "GETSALES_LIST_UUID n'est pas défini. Va sur la page 'Lists' de "
            "GetSales, clique les 3 points d'une liste, 'Copy List ID'."
        )
    linkedin_id = extract_linkedin_identifier(prospect["linkedin_url"])
    custom_fields = {}
    if note_connexion:
        custom_fields["Connection_Message"] = note_connexion
    if premier_message:
        custom_fields["First_Message"] = premier_message

    body = {
        "list_uuid": LIST_UUID,
        "leads": [
            {
                "linkedin_id": linkedin_id,
                "linkedin": linkedin_id,
                "first_name": prospect.get("prenom", ""),
                "last_name": prospect.get("nom", ""),
                "company_name": prospect.get("entreprise", ""),
                "position": prospect.get("poste", ""),
                "email": prospect.get("email") or None,
                "custom_fields": custom_fields,
            }
        ],
    }
    result = _request("POST", "/leads/api/leads", json=body)
    return result[0] if isinstance(result, list) else result


def send_message(lead_uuid: str, text: str) -> dict:
    """Envoie un message LinkedIn à un lead déjà créé dans GetSales.

    ⚠️ Pas vérifié empiriquement : la doc ne précise pas explicitement si
    cet endpoint gère aussi le cas "pas encore connecté" (invitation) ou
    s'il suppose une connexion déjà établie. Teste avec un contact que tu
    sais déjà connecté en premier, avant de lancer sur des non-connectés."""
    if not SENDER_PROFILE_UUID:
        raise RuntimeError(
            "GETSALES_SENDER_PROFILE_UUID n'est pas défini. Trouve-le sur "
            "la page 'Sender Profiles' de GetSales."
        )
    body = {
        "sender_profile_uuid": SENDER_PROFILE_UUID,
        "lead_uuid": lead_uuid,
        "text": text,
    }
    return _request("POST", "/flows/api/messages", json=body)


def list_messages(lead_uuid: str) -> list[dict]:
    """Historique des messages LinkedIn pour un lead donné (utile pour
    vérifier après coup qu'un envoi est bien parti, sans lecture automatisée)."""
    result = _request(
        "GET", "/flows/api/linkedin-messages", params={"filter[lead_uuid]": lead_uuid}
    )
    return result.get("data", []) if isinstance(result, dict) else []
