"""Le dialecte Microsoft Graph — ce qui le distingue de BoondManager.

Chaque test vise une différence qui casserait un client ayant confondu les
deux API : le flux OAuth2 (contre un credential statique), l'enveloppe
`value[]` (contre `data[]` + `meta.totals`), le curseur opaque
`@odata.nextLink` (contre `page`/`maxResults`), et l'enveloppe d'erreur
`{"error": {"code", "message"}}`.
"""

from __future__ import annotations

from conftest import (
    CLIENT_ID,
    CLIENT_SECRET,
    TENANT,
    app_module,
    forger_jeton,
    tous_les_groupes,
)

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


def test_l_autorite_parle_OAuth2_et_non_le_dialecte_de_Graph(client):
    """LE test de 0.3.0 : l'enveloppe du chemin du jeton était FAUSSE.

    Relevé contre `login.microsoftonline.com` le 2026-09-03. L'autorité rend
    l'enveloppe OAuth2, PLATE, avec un code `AADSTS…` — pas
    `{"error": {"code", "message"}}`, qui est celle de l'API de RESSOURCES.

    Le mock rendait celle de Graph. Un consommateur qui écrivait son diagnostic
    contre lui lisait `error.code`, et trouvait `None` en production — perdant
    le code AADSTS, seul élément qui sépare « secret faux » (7000215) de
    « application inconnue » (700038), deux pannes aux gestes opposés.

    Les statuts ci-dessous sont eux aussi observés : l'autorité rend 401 sur un
    secret faux, mais 400 sur les trois autres refus. Ce n'est donc PAS le
    statut qui distingue les deux hôtes, c'est l'enveloppe.
    """
    base = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials",
    }
    cas = [
        ({**base, "client_secret": "faux"}, TENANT, 401, "invalid_client", 7000215),
        (
            {**base, "client_id": "11111111-2222-3333-4444-555555555555"},
            TENANT,
            400,
            "unauthorized_client",
            700038,
        ),
        (base, "11111111-2222-3333-4444-555555555555", 400, "invalid_request", 900021),
        ({**base, "grant_type": "password"}, TENANT, 400, "unsupported_grant_type", 70003),
    ]
    for corps_envoye, tenant, statut, code, aadsts in cas:
        r = client.post(f"/{tenant}/oauth2/v2.0/token", data=corps_envoye)
        corps = r.json()
        assert r.status_code == statut, (code, r.text)
        # PLAT : `error` est une CHAÎNE, pas un objet. C'est la distinction.
        assert corps["error"] == code
        assert isinstance(corps["error"], str)
        assert corps["error_description"].startswith(f"AADSTS{aadsts}:")
        assert corps["error_codes"] == [aadsts]
        assert {"timestamp", "trace_id", "correlation_id"} <= set(corps)


def test_le_jeton_porte_ext_expires_in(client):
    """L'autorité réelle le rend à côté d'`expires_in`."""
    r = client.post(
        f"/{TENANT}/oauth2/v2.0/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
    )
    assert r.json()["ext_expires_in"] == r.json()["expires_in"]


# ── Authentification des appels Graph ────────────────────────────────────────


def test_sans_jeton_401_enveloppe_graph(client):
    """L'enveloppe d'erreur de Graph, pas celle de BoondManager."""
    r = client.get("/v1.0/groups")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "InvalidAuthenticationToken"
    assert "error" in r.json() and "errors" not in r.json()


def test_l_erreur_de_graph_porte_le_request_id_du_support(client):
    """`innerError.request-id` est l'identifiant qu'un ticket Microsoft exige.

    Relevé le 2026-09-03 : toute erreur de `graph.microsoft.com` porte
    `error.innerError` avec `date`, `request-id` et `client-request-id`. Le mock
    ne les rendait pas — un consommateur pouvait donc écrire un diagnostic qui
    n'en gardait aucune trace, et s'en apercevoir le jour d'une panne réelle.
    """
    corps = client.get("/v1.0/groups").json()
    interne = corps["error"]["innerError"]
    assert {"date", "request-id", "client-request-id"} <= set(interne)
    assert interne["request-id"]


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


# ── Ce que 0.3.0 ajoute : les trois écarts restants ─────────────────────────


def test_les_groupes_sont_PAGINES_comme_chez_microsoft(client, auth):
    """Microsoft sert 100 groupes par page ; le mock rendait tout d'un coup.

    Un consommateur qui suivait `@odata.nextLink` sur `/members` mais pas ici
    était vert en CI et tronquait au 101ᵉ groupe d'un annuaire réel — sans
    erreur : les membres des groupes non listés n'obtenaient aucun périmètre.
    C'est le défaut trouvé dans insights360 le 2026-09-03.
    """
    premiere = client.get("/v1.0/groups", headers=auth).json()
    assert "@odata.nextLink" in premiere, "la pagination des groupes n'est pas exercée"
    assert len(premiere["value"]) == app_module.GROUPS_PAGE_SIZE

    tous = tous_les_groupes(client, auth)
    assert len(tous) == len(premiere["value"]) + 1 or len(tous) > len(premiere["value"])
    assert len({g["id"] for g in tous}) == len(tous), "un groupe rendu deux fois"


def test_sans_select_le_jeu_par_defaut_est_LARGE(client, auth):
    """C'est la COLLECTE qui fonde l'obligation, pas le stockage.

    Le CSDL officiel déclare 79 propriétés sur `user`. Le mock en rendait
    quatre : un consommateur qui oubliait `$select` collectait, en production,
    le poste, le mobile, le service et le matricule — et rien, en CI, ne le lui
    disait. Le jeu par défaut est donc large ICI aussi, pour que l'oubli se
    voie gratuitement.
    """
    groupes = tous_les_groupes(client, auth)
    membre = client.get(f"/v1.0/groups/{groupes[0]['id']}/members", headers=auth).json()["value"][0]
    assert {"jobTitle", "mobilePhone", "department", "employeeId"} <= set(membre)


def test_le_select_est_HONORE_et_reporte_dans_le_lien_suivant(client, auth):
    """Demander deux champs doit en rendre deux — sur TOUTES les pages.

    Graph recopie `$select` dans le `@odata.nextLink`. Un mock qui ne le ferait
    pas rendrait la deuxième page plus large que la première, et masquerait un
    consommateur qui repasse ses paramètres à la main — lequel écraserait alors
    le curseur.
    """
    groupes = tous_les_groupes(client, auth)
    rh = next(g for g in groupes if g["displayName"] == "grp-bi-rh")

    url = f"/v1.0/groups/{rh['id']}/members?$select=id,userPrincipalName"
    pages = 0
    while url and pages < 10:
        corps = client.get(url, headers=auth).json()
        for membre in corps["value"]:
            # `@odata.type` est une ANNOTATION, pas une propriété : Graph la
            # rend toujours, elle ne se sélectionne pas.
            assert {c for c in membre if not c.startswith("@")} == {"id", "userPrincipalName"}
        suivant = corps.get("@odata.nextLink")
        url = suivant.replace("http://testserver", "") if suivant else ""
        pages += 1
    assert pages >= 2, "le report du $select sur la page suivante n'a pas été exercé"


def test_un_select_inconnu_rend_400_sur_TOUTE_la_collection(client, auth):
    """Graph 400 sur une propriété que le type ne porte pas.

    Un mock qui ignorerait le paramètre laisserait passer une faute de frappe
    jusqu'au premier run réel — où elle n'emporterait pas une ligne, mais la
    source entière.
    """
    groupes = tous_les_groupes(client, auth)
    r = client.get(
        f"/v1.0/groups/{groupes[0]['id']}/members?$select=id,userPrincipalNam", headers=auth
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "Request_UnsupportedQuery"


def test_l_etranglement_est_injectable_et_porte_retry_after(client, auth, monkeypatch):
    """Graph étrangle ; le mock ne l'avait jamais fait.

    Le profil d'appels d'une extraction d'appartenance — une liste, puis un
    appel paginé PAR GROUPE — est exactement celui qui le déclenche. Sans un
    429 injectable, aucun consommateur ne peut éprouver sa reprise, et la
    première rencontre a lieu en production.

    Éteint par défaut : un mock qui étrangle spontanément rendrait la CI
    intermittente.
    """
    monkeypatch.setattr(app_module, "THROTTLE_EVERY", 1)
    monkeypatch.setattr(app_module, "_appels", 0)

    r = client.get("/v1.0/groups", headers=auth)
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "1"
    assert r.json()["error"]["code"] == "TooManyRequests"


def test_les_identifiants_de_membres_sont_des_GUID(client, auth):
    """Un objet d'annuaire réel est identifié par un GUID, pas par son UPN.

    Le mock rendait `user-<upn>` : un consommateur pouvait en déduire que
    l'identifiant portait la clé de jointure. Il ne la porte pas.
    """
    groupes = tous_les_groupes(client, auth)
    membre = client.get(f"/v1.0/groups/{groupes[0]['id']}/members", headers=auth).json()["value"][0]
    assert len(membre["id"]) == 36 and membre["id"].count("-") == 4
    assert membre["userPrincipalName"] not in membre["id"]


def test_le_select_est_valide_contre_le_TYPE_et_non_contre_la_page(client, auth):
    """`$select=userPrincipalName` doit passer même sur une page sans personne.

    Défaut attrapé au premier run bout-en-bout d'insights360, le 2026-09-03 :
    le mock validait `$select` contre les objets de la page, donc rendait 400
    sur `grp-bi-imbrique`, dont aucun membre n'est un `user`. Graph valide
    contre le TYPE de la collection — `Collection(graph.directoryObject)` — et
    accepte.

    Un mock plus SÉVÈRE que le fournisseur est un défaut au même titre qu'un
    mock plus permissif : il fait échouer du code correct, et pousse à écrire
    du code faux pour le contenter.
    """
    groupes = {g["displayName"]: g["id"] for g in tous_les_groupes(client, auth)}
    r = client.get(
        f"/v1.0/groups/{groupes['grp-bi-imbrique']}/members?$select=id,userPrincipalName",
        headers=auth,
    )
    assert r.status_code == 200, r.text
    # La propriété est valide pour le type mais absente de ces objets : Graph
    # l'omet, il ne rend pas une clé nulle.
    assert all("userPrincipalName" not in m for m in r.json()["value"])
    assert all(set(m) <= {"id", "@odata.type"} for m in r.json()["value"])


def test_transitiveMembers_aplatit_ce_que_members_perd(client, auth):
    """LE test qui justifie l'endpoint, chiffré chez un vrai fournisseur.

    Sondé le 2026-09-03 contre un annuaire d'entreprise réel : un groupe
    transverse y rend UNE personne en direct et une TRENTAINE en transitif —
    dix-neuf groupes imbriqués. Quatre groupes de direction régionaux en
    perdent trois chacun.

    Un consommateur qui lit `/members` pour construire une ACL prive donc la
    quasi-totalité des ayants droit de leur périmètre, sans une erreur et sans
    un avertissement. Le mock ne servant pas cet endpoint, il ne POUVAIT pas se
    corriger sans casser sa stack locale : une absence qui enferme dans le
    défaut, pas seulement qui le masque.

    `grp-bi-imbrique` reproduit la forme en miniature : aucune personne en
    direct, une en transitif.
    """
    groupes = {g["displayName"]: g["id"] for g in tous_les_groupes(client, auth)}
    gid = groupes["grp-bi-imbrique"]

    def personnes(chemin: str) -> set[str]:
        vus: set[str] = set()
        url = f"/v1.0/groups/{gid}/{chemin}"
        pages = 0
        while url and pages < 20:
            corps = client.get(url, headers=auth).json()
            vus.update(m["userPrincipalName"] for m in corps["value"] if m.get("userPrincipalName"))
            suivant = corps.get("@odata.nextLink")
            url = suivant.replace("http://testserver", "") if suivant else ""
            pages += 1
        return vus

    directs, transitifs = personnes("members"), personnes("transitiveMembers")
    assert directs == set(), "le groupe imbriqué ne doit avoir AUCUN membre direct"
    assert transitifs == {"lars.marchand@boreal-conseil.example"}, transitifs


def test_transitiveMembers_rend_AUSSI_les_groupes_imbriques(client, auth):
    """Graph garde l'objet groupe dans la collection, en plus de ses membres.

    Les omettre cacherait la structure de l'annuaire à un consommateur qui
    voudrait la diagnostiquer — et ferait diverger le mock du fournisseur sur
    un point observable.
    """
    groupes = {g["displayName"]: g["id"] for g in tous_les_groupes(client, auth)}
    corps = client.get(
        f"/v1.0/groups/{groupes['grp-bi-imbrique']}/transitiveMembers?$skiptoken=0", headers=auth
    ).json()
    # La page vaut 1 : on parcourt pour voir tous les types.
    types = set()
    url = f"/v1.0/groups/{groupes['grp-bi-imbrique']}/transitiveMembers"
    pages = 0
    while url and pages < 20:
        corps = client.get(url, headers=auth).json()
        types.update(m["@odata.type"] for m in corps["value"])
        suivant = corps.get("@odata.nextLink")
        url = suivant.replace("http://testserver", "") if suivant else ""
        pages += 1
    assert "#microsoft.graph.group" in types
    assert "#microsoft.graph.user" in types


def test_transitiveMembers_ne_boucle_pas_sur_un_cycle(client, auth, monkeypatch):
    """Entra autorise A ∋ B ∋ A ; une récursion naïve y tournerait sans fin."""
    groupes = {g["displayName"]: g["id"] for g in tous_les_groupes(client, auth)}
    imbrique, sales = groupes["grp-bi-imbrique"], groupes["grp-bi-sales"]

    # On referme le cycle : grp-bi-sales contient grp-bi-imbrique, qui le contient.
    original = app_module.GROUPES[sales]["membres"]
    monkeypatch.setitem(
        app_module.GROUPES[sales],
        "membres",
        [*original, {"type": "#microsoft.graph.group", "id": imbrique}],
    )

    corps = client.get(f"/v1.0/groups/{imbrique}/transitiveMembers", headers=auth)
    assert corps.status_code == 200
