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


def dossier_profil(profil: str) -> Path:
    return CREDS_DIR / profil


def chemin_client_secret(profil: str) -> Path:
    return dossier_profil(profil) / "client_secret.json"


def chemin_token(profil: str) -> Path:
    return dossier_profil(profil) / "token.json"


def get_service(profil: str | None = None):
    """Retourne un client Gmail API authentifié POUR CE PROFIL — chaque
    profil a ses propres identifiants (credentials/<profil>/), puisque
    SAMMPO et un profil médical n'envoient pas forcément depuis la même
    boîte. Ouvre un navigateur pour l'autorisation au tout premier
    lancement de ce profil, puis réutilise le token stocké (avec
    rafraîchissement automatique) ensuite. profil=None retombe sur le
    profil actif — pratique en CLI, jamais utilisé par le dashboard qui
    précise toujours explicitement quel profil."""
    if profil is None:
        from db import database as db
        profil = db.profil_actif()

    client_secret_path = chemin_client_secret(profil)
    token_path = chemin_token(profil)

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not client_secret_path.exists():
                raise FileNotFoundError(
                    f"'{client_secret_path}' introuvable. Dans Paramètres, section "
                    f"Connexions du profil « {profil} », dépose le fichier d'identifiants "
                    "OAuth téléchargé depuis Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
            creds = flow.run_local_server(port=0)

        dossier_profil(profil).mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())

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


def get_my_email_address(service) -> str | None:
    """Adresse du compte Gmail authentifié (celui qui envoie). Sert à ne
    jamais confondre un email qu'on a NOUS-MÊMES envoyé (Sent, ou reçu en
    copie en s'auto-testant avec la même adresse pour l'envoi et le test)
    avec une vraie réponse de prospect — voir email_reader.collecter_nouveaux_emails.
    Retourne None plutôt que de lever, pour ne jamais faire échouer tout un
    scan à cause de ça (le scan continue simplement sans ce garde-fou)."""
    try:
        profil = service.users().getProfile(userId="me").execute()
    except Exception:  # noqa: BLE001
        return None
    return (profil.get("emailAddress") or "").strip().lower() or None


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


def send_message(service, to_email: str, subject: str, body_text: str,
                 thread_id: str | None = None, in_reply_to: str | None = None) -> dict:
    """Envoie un email texte brut. Ne fait AUCUNE vérification métier
    (validation humaine, désinscription...) — c'est la responsabilité de
    l'appelant (agents/email_sender.py) de s'en assurer avant d'appeler ceci.

    thread_id / in_reply_to (optionnels) rattachent l'email à un fil Gmail
    existant — utilisé pour les relances, afin qu'elles arrivent comme la
    suite du premier message plutôt que comme un email tout neuf dans la
    boîte du prospect. in_reply_to doit être l'en-tête Message-ID (RFC) du
    message auquel on répond, récupérable via get_rfc_message_id()."""
    message = MIMEText(body_text)
    message["to"] = to_email
    message["subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    body = {"raw": raw}
    if thread_id:
        body["threadId"] = thread_id
    return service.users().messages().send(userId="me", body=body).execute()


def get_rfc_message_id(service, message_id: str) -> str | None:
    """Récupère l'en-tête Message-ID (RFC, ex: '<abc123@mail.gmail.com>') d'un
    message qu'on vient d'envoyer — c'est cette valeur, pas l'id interne
    Gmail, qu'il faut fournir en in_reply_to à la relance suivante. Une
    requête légère (métadonnées seules, pas le corps). Retourne None si
    indisponible plutôt que de faire échouer l'envoi qui vient de réussir."""
    try:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="metadata", metadataHeaders=["Message-ID"])
            .execute()
        )
    except Exception:  # noqa: BLE001 - un échec ici ne doit jamais faire perdre l'email déjà envoyé
        return None
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    return headers.get("message-id")
