"""Le jeu de données — et surtout ses deux cas limites.

Ce mock ne vaut pas par son volume mais par ce qu'il permet de PROUVER à la
jointure avec le SIRH. Les quatre groupes correspondent aux quatre règles du
modèle d'autorisation d'insights360, et deux UPN existent uniquement pour
éprouver les bords :

  • `ext.consultant@ent.fr` — membre d'un groupe, ABSENT du SIRH ;
  • `tom.absent@ent.fr`     — présent au SIRH, membre d'AUCUN groupe.

Si ces deux-là disparaissaient, la suite d'autorisation aval passerait sans
plus rien démontrer.
"""

from __future__ import annotations


def _tous_les_membres(client, auth) -> set[str]:
    membres: set[str] = set()
    for groupe in client.get("/v1.0/groups", headers=auth).json()["value"]:
        url = f"/v1.0/groups/{groupe['id']}/members"
        while url:
            corps = client.get(url, headers=auth).json()
            membres.update(m["userPrincipalName"] for m in corps["value"])
            suivant = corps.get("@odata.nextLink")
            url = suivant.replace("http://testserver", "") if suivant else ""
    return membres


def test_les_quatre_groupes_des_quatre_regles(client, auth):
    noms = {g["displayName"] for g in client.get("/v1.0/groups", headers=auth).json()["value"]}
    assert noms == {"grp-bi-rh", "grp-bi-sales", "grp-bi-direction", "grp-comex"}


def test_le_membre_absent_du_sirh_est_present(client, auth):
    """Il DOIT être servi : le pipeline doit le laisser tomber à la jointure,
    jamais produire une ligne à clé inconnue — ce qui ferait dégénérer le
    prédicat de la couche interne en « pas de filtre »."""
    assert "ext.consultant@ent.fr" in _tous_les_membres(client, auth)


def test_le_collaborateur_sans_groupe_est_absent(client, auth):
    """`tom.absent` n'appartient à AUCUN groupe : il ne doit voir que
    lui-même (règle 1). Sa présence ici invaliderait le cas limite."""
    assert "tom.absent@ent.fr" not in _tous_les_membres(client, auth)


def test_comex_recoupe_direction(client, auth):
    """La règle 4 (visibilité totale) porte sur une personne qui a AUSSI une
    portée par groupe : c'est ce recoupement qui éprouve la déduplication de
    l'union des règles."""
    groupes = {
        g["displayName"]: g["id"] for g in client.get("/v1.0/groups", headers=auth).json()["value"]
    }
    comex = client.get(f"/v1.0/groups/{groupes['grp-comex']}/members", headers=auth).json()
    direction = client.get(
        f"/v1.0/groups/{groupes['grp-bi-direction']}/members", headers=auth
    ).json()
    assert comex["value"][0]["userPrincipalName"] == direction["value"][0]["userPrincipalName"]


def test_le_jeu_est_deterministe(client, auth):
    """Aucun aléa : deux lectures rendent le même monde. Le jeu est une
    FIXTURE, et les attentes aval s'y adossent."""
    assert _tous_les_membres(client, auth) == _tous_les_membres(client, auth)
