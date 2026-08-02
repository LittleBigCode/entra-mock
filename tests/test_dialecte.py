"""Le dialecte Microsoft Graph — ce qui le distingue de BoondManager.

Chaque test vise une différence qui casserait un client ayant confondu les
deux API : le flux OAuth2 (contre un credential statique), l'enveloppe
`value[]` (contre `data[]` + `meta.totals`), le curseur opaque
`@odata.nextLink` (contre `page`/`maxResults`), et l'enveloppe d'erreur
`{"error": {"code", "message"}}`.
"""

from __future__ import annotations

from conftest import CLIENT_ID, CLIENT_SECRET, TENANT, forger_jeton

# ── Le flux client credentials ───────────────────────────────────────────────


def test_le_jeton_est_un_bearer_expirant(client):
    r = client.post(
        f"/{TENANT}/oauth2/v2.0/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
    )
    assert r.status_code == 200
    corps = r.json()
    assert corps["token_type"] == "Bearer"
    assert corps["expires_in"] > 0
    assert corps["access_token"]


def test_le_corps_du_jeton_est_form_encode(client):
    """Graph poste un corps FORM-encodé. Envoyer du JSON doit échouer : un mock
    permissif laisserait passer un client incapable de parler à la vraie API."""
    r = client.post(
        f"/{TENANT}/oauth2/v2.0/token",
        json={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
    )
    assert r.status_code >= 400


def test_mauvais_secret_puis_mauvais_tenant_puis_mauvais_grant(client):
    base = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials",
    }
    r = client.post(f"/{TENANT}/oauth2/v2.0/token", data={**base, "client_secret": "faux"})
    assert (r.status_code, r.json()["error"]["code"]) == (401, "invalid_client")

    r = client.post("/11111111-2222-3333-4444-555555555555/oauth2/v2.0/token", data=base)
    assert (r.status_code, r.json()["error"]["code"]) == (400, "invalid_request")

    r = client.post(f"/{TENANT}/oauth2/v2.0/token", data={**base, "grant_type": "password"})
    assert (r.status_code, r.json()["error"]["code"]) == (400, "unsupported_grant_type")


# ── Authentification des appels Graph ────────────────────────────────────────


def test_sans_jeton_401_enveloppe_graph(client):
    """L'enveloppe d'erreur de Graph, pas celle de BoondManager."""
    r = client.get("/v1.0/groups")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "InvalidAuthenticationToken"
    assert "error" in r.json() and "errors" not in r.json()


def test_jeton_expire_refuse(client):
    """L'expiration est vérifiée POUR DE BON — c'est ce qui permet d'éprouver
    le renouvellement côté client, la principale différence opérationnelle
    avec le credential statique de BoondManager."""
    perime = forger_jeton(expire_dans=-60)
    r = client.get("/v1.0/groups", headers={"Authorization": f"Bearer {perime}"})
    assert r.status_code == 401


def test_jeton_d_une_autre_audience_refuse(client):
    autre = forger_jeton(aud="99999999-9999-9999-9999-999999999999")
    r = client.get("/v1.0/groups", headers={"Authorization": f"Bearer {autre}"})
    assert r.status_code == 401


def test_jeton_illisible_refuse(client):
    r = client.get("/v1.0/groups", headers={"Authorization": "Bearer pas-du-base64!!"})
    assert r.status_code == 401


# ── Enveloppe et pagination ──────────────────────────────────────────────────


def test_enveloppe_value_et_odata_context(client, auth):
    r = client.get("/v1.0/groups", headers=auth)
    assert r.status_code == 200
    corps = r.json()
    assert "value" in corps and "@odata.context" in corps
    # Les marqueurs de l'autre dialecte ne doivent PAS apparaître.
    assert "data" not in corps and "meta" not in corps


def test_pagination_par_curseur_opaque(client, auth):
    """Le groupe grp-bi-rh a deux membres et la page vaut 1 : le second n'est
    accessible QUE par @odata.nextLink. Un client qui ignorerait ce lien ne
    verrait que le premier — en silence, sans la moindre erreur."""
    groupes = client.get("/v1.0/groups", headers=auth).json()["value"]
    rh = next(g for g in groupes if g["displayName"] == "grp-bi-rh")

    url = f"/v1.0/groups/{rh['id']}/members"
    vus: list[str] = []
    pages = 0
    while url and pages < 10:
        corps = client.get(url, headers=auth).json()
        vus.extend(m["userPrincipalName"] for m in corps["value"])
        suivant = corps.get("@odata.nextLink")
        url = suivant.replace("http://testserver", "") if suivant else ""
        pages += 1

    assert pages >= 2, "la pagination n'a pas été exercée"
    assert len(vus) == 2
    # La dernière page ne porte PAS de lien suivant : c'est le signal d'arrêt.
    assert (
        "@odata.nextLink"
        not in client.get(f"/v1.0/groups/{rh['id']}/members?$skiptoken=1", headers=auth).json()
    )


def test_membre_porte_upn_et_mail(client, auth):
    groupes = client.get("/v1.0/groups", headers=auth).json()["value"]
    corps = client.get(f"/v1.0/groups/{groupes[0]['id']}/members", headers=auth).json()
    membre = corps["value"][0]
    # `userPrincipalName` est LA clé du modèle d'autorisation aval — `mail`
    # existe aussi et les deux divergent dans une organisation réelle.
    assert membre["userPrincipalName"]
    assert membre["@odata.type"] == "#microsoft.graph.user"


def test_groupe_inconnu_404_enveloppe_graph(client, auth):
    r = client.get("/v1.0/groups/11111111-0000-0000-0000-999999999999/members", headers=auth)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "Request_ResourceNotFound"


def test_health_non_authentifie(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "entra-mock"}
