# entra-mock

A mock of the **Microsoft Graph** endpoints used for **Entra group
membership** — the OAuth2 client-credentials flow, `/v1.0/groups` and
`/v1.0/groups/{id}/members` with opaque-cursor pagination.

Extracted from the `mock/` directory of
[insights360](https://github.com/LittleBigCode/insights360) so it follows the
same shape as the other mocks of the ecosystem
([boondmanager-mock](https://github.com/LittleBigCode/boondmanager-mock),
[linkedin-mock](https://github.com/LittleBigCode/linkedin-mock),
[ga-mock](https://github.com/LittleBigCode/ga-mock)): its own repository, a
published container image, a smoke-tested CI, and tests that live with the
code they protect.

## Why this mock is separate from the BoondManager one

Entra group membership is **not** a BoondManager resource, contrary to what
the insights360 spec suggests (§4.2 — see `docs/SPEC-DEVIATIONS.md` #4 in that
repo). Nothing is shared:

|  | BoondManager | Microsoft Graph |
|---|---|---|
| auth | static HS256 JWT in a custom header | OAuth2 client credentials → **expiring** Bearer |
| envelope | `data[]` + `meta.totals.rows` | `value[]` + `@odata.nextLink` |
| pagination | `page` / `maxResults` | **opaque cursor** in a URL |
| errors | `{"errors": [{status, code, detail}]}` | `{"error": {"code", "message"}}` |

Mixing them in one client produces a client that lies about what it speaks.

## Start in one command

```bash
docker run --rm -p 8011:8000 ghcr.io/littlebigcode/entra-mock:latest   # prebuilt
docker compose up --build                                              # local build
make bootstrap && make run                                             # from source
```

Then:

```bash
TENANT=00000000-0000-0000-0000-000000000000
curl http://localhost:8011/health

TOKEN=$(curl -s -X POST \
  -d "client_id=$TENANT" -d 'client_secret=change-me-entra' \
  -d 'grant_type=client_credentials' \
  "http://localhost:8011/$TENANT/oauth2/v2.0/token" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

curl -H "Authorization: Bearer $TOKEN" http://localhost:8011/v1.0/groups
```

(`make run` binds 8000; compose maps host port **8011** — the insights360
allocation: boond 8010, entra 8011, linkedin 8012, ga 8013.)

## Served surface

| Endpoint | Notes |
|---|---|
| `POST /{tenant}/oauth2/v2.0/token` | client-credentials flow; **form-encoded body** (JSON is refused, as in the real API); yields a Bearer with a real expiry |
| `GET /v1.0/groups` | the four groups, `value[]` + `@odata.context` |
| `GET /v1.0/groups/{id}/members` | members paginated by **opaque cursor** (`@odata.nextLink`); last page carries no link |
| `GET /health` | unauthenticated probe |

Tokens are checked for real: audience **and** expiry. That is what makes the
client-side renewal path testable — the main operational difference with
BoondManager's static credential.

## The dataset, and the two edge cases that justify it

Four groups, one per authorization rule of insights360
(`grp-bi-rh`, `grp-bi-sales`, `grp-bi-direction` → rule 3, scope by group;
`grp-comex` → rule 4, full visibility). The UPNs line up with the
BoondManager mock's dataset, and two of them exist **only** to exercise the
join between the two sources:

- **`ext.consultant@ent.fr`** — a group member who is **absent from the HR
  system**. The pipeline must drop it at the join, never emit a row with an
  unknown key (which would degrade the inner-layer predicate into "no
  filter");
- **`tom.absent@ent.fr`** — present in HR, member of **no group**. He must see
  only himself (rule 1).

`grp-comex` deliberately overlaps `grp-bi-direction`: that overlap is what
exercises the deduplication of the rule union.

**Page size defaults to 1** (`ENTRA_MOCK_PAGE_SIZE`). That is not a
performance choice: with a page of one, *every* group of more than one member
is paginated, so the `@odata.nextLink` path is exercised by construction
rather than by luck. A pipeline that ignored the link would see only the first
member — silently, without any error.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ENTRA_TENANT_ID` | `00000000-0000-0000-0000-000000000000` | tenant accepted on the token path |
| `ENTRA_CLIENT_ID` | `00000000-0000-0000-0000-000000000000` | client id, and token audience |
| `ENTRA_CLIENT_SECRET` | `change-me-entra` | client secret |
| `ENTRA_MOCK_TOKEN_TTL` | `3600` | token lifetime in seconds — lower it to rehearse renewal |
| `ENTRA_MOCK_PAGE_SIZE` | `1` | members per page (see above) |
| `ENTRA_MOCK_UPN_DOMAIN` | `ent.fr` | UPN domain of the dataset |
| `ENTRA_MOCK_HOST` / `_PORT` | `0.0.0.0` / `8000` | uvicorn bind |

## Development

```bash
make bootstrap   # uv sync
make test        # pytest — OAuth2 flow, pagination, dialect, dataset
make lint        # ruff + mypy --strict
make contract    # regenerate contracts/msgraph.openapi.yaml
```

Version `0.1.0` is bumped in lockstep in `pyproject.toml`,
`src/entra_mock/app.py` (`FastAPI(version=…)`) and `docker-compose.yml`.
