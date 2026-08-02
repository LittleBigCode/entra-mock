"""Harnais de tests — l'application montée en process.

La propriété qu'il ne faut pas perdre : l'application que la stack interroge
EST celle que les tests exercent.
"""

from __future__ import annotations

import base64
import json
import time

import pytest
from fastapi.testclient import TestClient

import entra_mock as mock

TENANT = "00000000-0000-0000-0000-000000000000"
CLIENT_ID = "00000000-0000-0000-0000-000000000000"
CLIENT_SECRET = "change-me-entra"


@pytest.fixture()
def client():
    return TestClient(mock.app)


@pytest.fixture()
def jeton(client) -> str:
    """Un jeton obtenu par le VRAI flux — jamais fabriqué à la main : c'est le
    chemin d'authentification que le consommateur doit emprunter."""
    r = client.post(
        f"/{TENANT}/oauth2/v2.0/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
    )
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


@pytest.fixture()
def auth(jeton: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jeton}"}


def forger_jeton(*, aud: str = CLIENT_ID, expire_dans: int = 3600) -> str:
    """Un jeton fabriqué — sert UNIQUEMENT à éprouver les refus (expiration,
    mauvaise audience) que le flux normal ne sait pas produire."""
    charge = json.dumps({"aud": aud, "exp": int(time.time()) + expire_dans}).encode()
    return base64.urlsafe_b64encode(charge).rstrip(b"=").decode()
