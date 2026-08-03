"""Mock Microsoft Graph — l'appartenance aux groupes Entra.

┌─ POURQUOI CE MOCK EST SÉPARÉ DE CELUI DE BOONDMANAGER ──────────────────────┐
│ La spec range « Entra group membership » sous §4.2, avec les ressources     │
│ BoondManager. C'est une erreur : ce n'est pas la même API.                  │
│                                                                             │
│                    BoondManager              Microsoft Graph                │
│   auth             JWT HS256 statique        OAuth2 client credentials      │
│                    en en-tête custom         → jeton Bearer expirant        │
│   enveloppe        data[] + meta.totals      value[] + @odata.nextLink      │
│   pagination       page / maxResults         curseur opaque dans une URL    │
│                                                                             │
│ Les mélanger dans un même client produirait un client qui ment sur ce qu'il │
│ parle. Cf. docs/SPEC-DEVIATIONS.md #4.                                      │
└─────────────────────────────────────────────────────────────────────────────┘

Le jeu de données est aligné sur le profil `insights360` de boondmanager-mock :
les mêmes UPN, et surtout les deux cas limites qui n'existent QUE dans la
jonction des deux sources —

  • `ext.consultant@boreal-conseil.example` : dans un groupe, ABSENT du SIRH ;
  • `kevin.silva@boreal-conseil.example`     : au SIRH, dans AUCUN groupe.
"""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Le domaine du jeu `realiste` de boondmanager-mock. L'UPN est la SEULE clé de
# jointure entre l'annuaire et le SIRH : un domaine qui diverge ne produit pas
# d'erreur, il produit zéro appartenance — donc zéro visibilité, donc des tests
# verts par vacuité.
UPN_DOMAIN = os.environ.get("ENTRA_MOCK_UPN_DOMAIN", "boreal-conseil.example")
TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "00000000-0000-0000-0000-000000000000")
CLIENT_ID = os.environ.get("ENTRA_CLIENT_ID", "00000000-0000-0000-0000-000000000000")
CLIENT_SECRET = os.environ.get("ENTRA_CLIENT_SECRET", "change-me-entra")

# Taille de page volontairement MINIMALE : Graph pagine avec un curseur opaque,
# et un pipeline qui ne suivrait pas `@odata.nextLink` ne verrait que les
# premiers membres — en silence, sans la moindre erreur. Avec une page de 1,
# TOUT groupe de plus d'un membre est paginé : le chemin de pagination est donc
# exercé par construction, pas par chance.
PAGE_SIZE = int(os.environ.get("ENTRA_MOCK_PAGE_SIZE", "1"))


def _d(upn: str) -> str:
    return upn.format(d=UPN_DOMAIN)


# ── L'appartenance aux groupes ───────────────────────────────────────────────
#
# Les quatre groupes correspondent aux quatre règles d'autorisation :
#   grp-bi-rh / grp-bi-sales / grp-bi-direction → règle 3 (portée du groupe)
#   grp-comex                                   → règle 4 (visibilité totale)
GROUPES: dict[str, dict[str, Any]] = {
    "11111111-0000-0000-0000-000000000001": {
        "displayName": "grp-bi-rh",
        "membres": [
            # Manager de niveau 2, agence Paris. Sa portée de groupe (règle 3)
            # lui donne les rattachés de son périmètre.
            _d("yuki.lambert@{d}"),
            # Membre d'un groupe, INCONNU du SIRH. Le pipeline doit le laisser
            # tomber à la jointure sur dim_collaborateur, jamais produire une
            # ligne à clé inconnue — ce qui ferait dégénérer le prédicat interne
            # en « pas de filtre ».
            _d("ext.consultant@{d}"),
        ],
    },
    "11111111-0000-0000-0000-000000000002": {
        "displayName": "grp-bi-sales",
        "membres": [_d("lars.marchand@{d}")],
    },
    "11111111-0000-0000-0000-000000000003": {
        "displayName": "grp-bi-direction",
        "membres": [_d("arthur.ivanov@{d}")],
    },
    "11111111-0000-0000-0000-000000000004": {
        "displayName": "grp-comex",
        # Le sommet de la hiérarchie, seul sans manager. Le Comex lui donne
        # TOUS les collaborateurs, Nantes compris — alors que le périmètre RLS
        # externe de bi_rh et bi_sales exclut Nantes. C'est ce couple qui fait
        # travailler l'invariant `inner ⊆ outer` : il tient PARCE QUE le RLS
        # rogne, et échouerait bruyamment si la requête interne était un jour
        # exécutée hors du rôle. Sans lui, l'invariant serait vrai par vacuité.
        "membres": [_d("arthur.ivanov@{d}")],
    },
}

# `kevin.silva@…` n'apparaît DÉLIBÉRÉMENT nulle part : présent au SIRH, membre
# d'aucun groupe. Il ne doit voir que lui-même (règle 1).
#
# ⚠️ Ces UPN sont ceux du jeu `realiste` de boondmanager-mock. Ils ne sont PAS
#    arbitraires : chacun est choisi pour la propriété qu'il rend testable
#    (sommet sans manager, agence hors périmètre, membre hors SIRH). Un
#    changement de jeu de données côté mock BoondManager casse ce fichier — et
#    c'est voulu : mieux vaut un test rouge qu'un modèle d'autorisation dont les
#    cas limites ont silencieusement cessé d'exister.

app = FastAPI(title="Microsoft Graph mock (Entra groups)", version="0.2.0")


def _erreur(status: int, code: str, message: str) -> JSONResponse:
    """L'enveloppe d'erreur de Graph — différente de celle de BoondManager."""
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _bearer_valide(request: Request) -> bool:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    jeton = header.removeprefix("Bearer ")
    try:
        charge = json.loads(base64.urlsafe_b64decode(jeton + "=" * (-len(jeton) % 4)))
    except Exception:
        return False
    # L'expiration est vérifiée pour de bon : c'est ce qui permet de tester le
    # renouvellement côté client, la principale différence opérationnelle avec
    # le credential statique de BoondManager.
    return bool(charge.get("aud") == CLIENT_ID and charge.get("exp", 0) > time.time())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "entra-mock"}


@app.post("/{tenant}/oauth2/v2.0/token")
async def token(tenant: str, request: Request) -> JSONResponse:
    """Flux client credentials — le vrai chemin d'authentification de Graph."""
    formulaire = await request.form()
    if tenant != TENANT_ID:
        return _erreur(400, "invalid_request", f"unknown tenant {tenant}")
    if formulaire.get("client_id") != CLIENT_ID or formulaire.get("client_secret") != CLIENT_SECRET:
        return _erreur(401, "invalid_client", "client authentication failed")
    if formulaire.get("grant_type") != "client_credentials":
        return _erreur(400, "unsupported_grant_type", "only client_credentials is supported")

    duree = int(os.environ.get("ENTRA_MOCK_TOKEN_TTL", "3600"))
    charge = json.dumps({"aud": CLIENT_ID, "exp": int(time.time()) + duree}).encode()
    jeton = base64.urlsafe_b64encode(charge).rstrip(b"=").decode()
    return JSONResponse({"token_type": "Bearer", "expires_in": duree, "access_token": jeton})


@app.get("/v1.0/groups")
def groupes(request: Request) -> JSONResponse:
    if not _bearer_valide(request):
        return _erreur(401, "InvalidAuthenticationToken", "Access token is empty or expired.")
    return JSONResponse(
        {
            "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#groups",
            "value": [{"id": gid, "displayName": g["displayName"]} for gid, g in GROUPES.items()],
        }
    )


@app.get("/v1.0/groups/{group_id}/members")
def membres(group_id: str, request: Request) -> JSONResponse:
    """Membres d'un groupe, paginés par curseur opaque — comme Graph.

    Le curseur est ici l'index de départ encodé dans `@odata.nextLink`. Un
    pipeline qui ne suivrait pas ce lien ne verrait que les PAGE_SIZE premiers
    membres, sans la moindre erreur. C'est pourquoi la page est petite.
    """
    if not _bearer_valide(request):
        return _erreur(401, "InvalidAuthenticationToken", "Access token is empty or expired.")
    groupe = GROUPES.get(group_id)
    if groupe is None:
        return _erreur(404, "Request_ResourceNotFound", f"group {group_id} does not exist")

    depuis = int(request.query_params.get("$skiptoken", "0"))
    tranche = groupe["membres"][depuis : depuis + PAGE_SIZE]
    corps: dict[str, Any] = {
        "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#directoryObjects",
        "value": [
            {
                "@odata.type": "#microsoft.graph.user",
                "id": f"user-{upn}",
                "userPrincipalName": upn,
                "mail": upn,
            }
            for upn in tranche
        ],
    }
    suivant = depuis + PAGE_SIZE
    if suivant < len(groupe["membres"]):
        base = str(request.base_url).rstrip("/")
        corps["@odata.nextLink"] = f"{base}/v1.0/groups/{group_id}/members?$skiptoken={suivant}"
    return JSONResponse(corps)
