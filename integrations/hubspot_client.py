"""
Client HubSpot — LECTURE SEULE (import de contacts), via un token d'App
privée HubSpot.

Pourquoi une App privée plutôt qu'un vrai "Connecter avec HubSpot" en un
clic ? Un vrai bouton OAuth demanderait d'enregistrer une app OAuth dans un
compte développeur HubSpot séparé (redirect URI, client_id/secret, parfois
une revue HubSpot) — une procédure lourde, pensée pour publier une app à
d'autres, pas pour connecter TON propre compte à TON propre outil. Les Apps
privées sont exactement ce que HubSpot recommande pour ce cas : un token
généré en quelques clics dans Réglages -> Intégrations -> Applications
privées de TON compte HubSpot, collé une fois dans Paramètres, et c'est fait.

Scope requis sur l'App privée : crm.objects.contacts.read
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api.hubapi.com"

# Propriétés HubSpot standard qu'on sait mapper vers nos champs. Un compte
# HubSpot peut avoir plein d'autres propriétés (custom), mais on ne devine
# pas des noms de propriétés propres à un compte précis.
PROPRIETES_CONTACT = ["firstname", "lastname", "jobtitle", "company", "email"]


class ErreurHubSpot(Exception):
    """Erreur HubSpot avec un message déjà prêt à afficher à l'utilisateur."""


def _appel(token: str, chemin: str, params: dict | None = None) -> dict:
    qs = ""
    if params:
        paires = [f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items() if v is not None]
        qs = "?" + "&".join(paires) if paires else ""
    requete = urllib.request.Request(
        f"{BASE_URL}{chemin}{qs}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(requete, timeout=15) as reponse:
            return json.loads(reponse.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise ErreurHubSpot("Token HubSpot invalide ou expiré.") from exc
        if exc.code == 403:
            raise ErreurHubSpot(
                "Token valide mais scope manquant — vérifie que "
                "'crm.objects.contacts.read' est coché sur l'App privée."
            ) from exc
        corps = exc.read().decode("utf-8", errors="replace")
        raise ErreurHubSpot(f"Erreur HubSpot ({exc.code}) : {corps[:200]}") from exc
    except urllib.error.URLError as exc:
        raise ErreurHubSpot(f"Impossible de joindre HubSpot : {exc.reason}") from exc


def tester_connexion(token: str) -> dict:
    """Vérifie que le token fonctionne. Retourne un petit résumé (nombre de
    contacts sur la première page) pour confirmer visuellement que ça marche."""
    donnees = _appel(token, "/crm/v3/objects/contacts", {"limit": 1})
    a_une_suite = bool(donnees.get("paging", {}).get("next"))
    return {"ok": True, "nb_contacts_page": len(donnees.get("results", [])), "plus_de_contacts": a_une_suite}


def lister_contacts(token: str, after: str | None = None, limit: int = 100) -> tuple[list[dict], str | None]:
    """Une page de contacts bruts HubSpot. Retourne (contacts, curseur pour
    la page suivante, ou None si c'était la dernière)."""
    donnees = _appel(token, "/crm/v3/objects/contacts", {
        "limit": limit, "after": after,
        "properties": ",".join(PROPRIETES_CONTACT),
    })
    resultats = donnees.get("results", [])
    curseur = donnees.get("paging", {}).get("next", {}).get("after")
    return resultats, curseur


def contact_vers_prospect(contact: dict) -> dict:
    """Convertit un contact HubSpot brut en dict compatible avec
    db.add_prospect() (les champs qu'on ne sait pas mapper restent vides —
    pas de linkedin_url : ce n'est pas une propriété standard HubSpot, son
    nom varie d'un compte à l'autre selon comment elle a été configurée)."""
    p = contact.get("properties", {})
    return {
        "prenom": p.get("firstname") or "",
        "nom": p.get("lastname") or "",
        "poste": p.get("jobtitle") or "",
        "entreprise": p.get("company") or "",
        "email": p.get("email") or None,
        "source": "hubspot",
    }
