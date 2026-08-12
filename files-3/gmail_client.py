"""
Client Gmail — lecture (gmail.readonly) + envoi (gmail.send).

L'envoi n'est utilisé QUE par agents/email_sender.py, et uniquement après
validation humaine explicite à chaque email — voir ce fichier pour le détail
du garde-fou. Ce module en lui-même n'impose aucune validation : c'est
volontairement la responsabilité de l'appelant, pas du client Gmail.

Setup requis avant la première utilisation (voir README.md) :
    1. Créer un projet sur console.cloud.google.com
    2. Activer l'API Gmail
    3. Configurer l'écran de consentement OAuth (externe, toi comme testeur)
    4. Créer un identifiant OAuth "Application de bureau"
    5. Télécharger le JSON, le renommer client_secret.json,
       le placer dans credentials/

⚠️ Si tu avais déjà autorisé l'app avec le scope readonly seul (pour
email_reader.py), supprime credentials/token.json et relance une commande
pour ré-autoriser avec le nouveau scope d'envoi inclus.
"""
from __future__ import annotations

import base64
from email.mime.text import MIMEText
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
CREDS_DIR = Path(__file__).parent.parent / "credentials"
CLIENT_SECRET_PATH = CREDS_DIR / "client_secret.json"
TOKEN_PATH = CREDS_DIR / "token.json"


def get_service():
    """Retourne un client Gmail API authentifié. Ouvre un navigateur pour
    l'autorisation au tout premier lancement, puis réutilise le token
    stocké (avec rafraîchissement automatique) ensuite."""
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CLIENT_SECRET_PATH.exists():
                raise FileNotFoundError(
                    f"'{CLIENT_SECRET_PATH}' introuvable. Télécharge le fichier "
                    "d'identifiants OAuth depuis Google Cloud Console (voir "
                    "README.md) et place-le à cet endroit exact."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
            creds = flow.run_local_server(port=0)

        CREDS_DIR.mkdir(exist_ok=True)
        TOKEN_PATH.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def search_messages(service, query: str, max_results: int = 20) -> list[dict]:
    """Retourne une liste de {id, threadId} correspondant à la requête
    Gmail (syntaxe identique à la barre de recherche Gmail, ex: 'from:x@y.com')."""
    result = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )
    return result.get("messages", [])


def get_message_content(service, message_id: str) -> dict:
    """Récupère et parse un message : expéditeur, sujet, date, corps texte."""
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    return {
        "id": message_id,
        "thread_id": msg.get("threadId"),
        "de": headers.get("from", ""),
        "sujet": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "corps": _extract_body(msg["payload"]),
    }


def _extract_body(payload: dict) -> str:
    """Parcourt récursivement les parties MIME pour trouver le texte brut ;
    retombe sur le HTML (non nettoyé) si aucun texte brut n'existe."""
    if payload.get("mimeType") == "text/plain" and "data" in payload.get("body", {}):
        return _decode(payload["body"]["data"])

    html_fallback = None
    for part in payload.get("parts", []) or []:
        if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
            return _decode(part["body"]["data"])
        if part.get("mimeType") == "text/html" and "data" in part.get("body", {}):
            html_fallback = _decode(part["body"]["data"])
        if part.get("parts"):
            nested = _extract_body(part)
            if nested:
                return nested

    if html_fallback:
        return html_fallback
    if "data" in payload.get("body", {}):
        return _decode(payload["body"]["data"])
    return ""


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")


def send_message(service, to_email: str, subject: str, body_text: str) -> dict:
    """Envoie un email texte brut. Ne fait AUCUNE vérification métier
    (validation humaine, désinscription...) — c'est la responsabilité de
    l'appelant (agents/email_sender.py) de s'en assurer avant d'appeler ceci."""
    message = MIMEText(body_text)
    message["to"] = to_email
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    return service.users().messages().send(userId="me", body={"raw": raw}).execute()
