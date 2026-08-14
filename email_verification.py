"""
Vérification d'email — syntaxe + existence du domaine (MX), gratuite, sans
service tiers payant (pas de Neverbounce/ZeroBounce).

Volontairement limitée à deux choses :
1. La syntaxe est valide.
2. Le domaine peut recevoir du courrier (a un enregistrement MX, ou à
   défaut un A/AAAA — RFC 5321 autorise ce repli).

Ce qu'on ne fait PAS : vérifier que LA BOÎTE précise existe (ça demande une
poignée de main SMTP jusqu'au serveur destinataire). Beaucoup de serveurs
mail traitent ces sondes comme suspectes ou les bloquent, et le taux de
faux négatifs est élevé — pas fiable, donc pas fait.

Principe : dans le doute, on ne bloque jamais. Un email n'est marqué
"invalide" que sur un problème confirmé (syntaxe cassée, domaine confirmé
sans MX ni A). Un timeout réseau ou une erreur de résolution reste "inconnu"
et n'empêche jamais un envoi.
"""
from __future__ import annotations

import re

import dns.resolver

REGEX_EMAIL = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)

DELAI_DNS = 3.0  # secondes — au-delà, on abandonne plutôt que de faire attendre l'utilisateur


def verifier(email: str) -> dict:
    """Retourne {statut, raison} où statut est 'valide' | 'invalide' | 'inconnu'.
    'invalide' UNIQUEMENT sur un problème confirmé — jamais sur un doute."""
    email = (email or "").strip()
    if not email:
        return {"statut": "inconnu", "raison": "Aucune adresse à vérifier."}
    if not REGEX_EMAIL.match(email):
        return {"statut": "invalide", "raison": "Syntaxe d'email invalide."}

    domaine = email.rsplit("@", 1)[-1]
    resolveur = dns.resolver.Resolver()
    resolveur.timeout = DELAI_DNS
    resolveur.lifetime = DELAI_DNS

    try:
        resolveur.resolve(domaine, "MX")
        return {"statut": "valide", "raison": "Domaine avec enregistrement MX."}
    except dns.resolver.NXDOMAIN:
        return {"statut": "invalide", "raison": f"Le domaine '{domaine}' n'existe pas."}
    except dns.resolver.NoAnswer:
        # Pas de MX explicite : repli RFC 5321 sur A/AAAA avant de conclure.
        try:
            resolveur.resolve(domaine, "A")
            return {"statut": "valide", "raison": "Pas de MX, mais le domaine répond (A)."}
        except dns.resolver.NXDOMAIN:
            return {"statut": "invalide", "raison": f"Le domaine '{domaine}' n'a ni MX ni A — ne peut pas recevoir d'email."}
        except Exception as exc:  # noqa: BLE001 - timeout, réseau, etc. : on ne bloque pas
            return {"statut": "inconnu", "raison": f"Vérification impossible ({exc})."}
    except Exception as exc:  # noqa: BLE001 - timeout, réseau, resolveur indisponible...
        return {"statut": "inconnu", "raison": f"Vérification impossible ({exc})."}
