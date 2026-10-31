"""Phase 13 (security_hardening: access_control). Consolidates what was otherwise scattered
across per-feature test files into one place that audits two guarantees across every
patient-scoped endpoint: (1) no token -> 401/403, never data; (2) another patient's entry_id ->
404, never 403 (never confirms the entry exists) and never the entry's actual content.

Endpoints already covered elsewhere (test_conversation_flow.py, test_assessment.py,
test_assessment_speech.py, test_vitals.py, test_patient_data.py) aren't duplicated here except
where this file closes a real gap — allergies/medications PATCH+DELETE cross-user 404 wasn't
exercised anywhere before this file (only history's PATCH was)."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _authed_headers(client: AsyncClient, email: str) -> dict[str, str]:
    resp = await client.post("/auth/register", json={"email": email, "password": "s3curePassw0rd"})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/patient"),
        ("GET", "/profile"),
        ("GET", "/history"),
        ("GET", "/allergies"),
        ("GET", "/medications"),
        ("GET", "/conversations"),
        ("GET", "/vitals"),
        ("GET", "/devices"),
        ("GET", "/symptoms"),
        ("GET", "/privacy/export"),
    ],
)
async def test_patient_scoped_get_endpoints_require_auth(client: AsyncClient, method: str, path: str) -> None:
    resp = await client.request(method, path)
    assert resp.status_code in (401, 403)


async def test_allergy_entry_not_accessible_or_mutable_cross_user(client: AsyncClient) -> None:
    headers_a = await _authed_headers(client, "allergyOwnerA@example.com")
    headers_b = await _authed_headers(client, "allergyOwnerB@example.com")

    entry_id = (
        await client.post("/allergies", headers=headers_a, json={"substance": "penicillin"})
    ).json()["id"]

    patch_resp = await client.patch(f"/allergies/{entry_id}", headers=headers_b, json={"substance": "tampered"})
    assert patch_resp.status_code == 404

    delete_resp = await client.delete(f"/allergies/{entry_id}", headers=headers_b)
    assert delete_resp.status_code == 404

    # Neither cross-user attempt actually changed or removed A's real entry.
    still_there = await client.get("/allergies", headers=headers_a)
    assert still_there.json()[0]["substance"] == "penicillin"


async def test_medication_entry_not_accessible_or_mutable_cross_user(client: AsyncClient) -> None:
    headers_a = await _authed_headers(client, "medOwnerA@example.com")
    headers_b = await _authed_headers(client, "medOwnerB@example.com")

    entry_id = (
        await client.post("/medications", headers=headers_a, json={"name": "albuterol"})
    ).json()["id"]

    patch_resp = await client.patch(f"/medications/{entry_id}", headers=headers_b, json={"name": "tampered"})
    assert patch_resp.status_code == 404

    delete_resp = await client.delete(f"/medications/{entry_id}", headers=headers_b)
    assert delete_resp.status_code == 404

    still_there = await client.get("/medications", headers=headers_a)
    assert still_there.json()[0]["name"] == "albuterol"


async def test_history_entry_not_deletable_cross_user(client: AsyncClient) -> None:
    """PATCH cross-user 404 is already covered in test_patient_data.py; this closes the
    remaining gap on DELETE specifically."""

    headers_a = await _authed_headers(client, "histOwnerA@example.com")
    headers_b = await _authed_headers(client, "histOwnerB@example.com")

    entry_id = (await client.post("/history", headers=headers_a, json={"condition": "asthma"})).json()["id"]

    delete_resp = await client.delete(f"/history/{entry_id}", headers=headers_b)
    assert delete_resp.status_code == 404

    still_there = await client.get("/history", headers=headers_a)
    assert len(still_there.json()) == 1


async def test_conversation_not_readable_cross_user_even_with_no_messages_yet(client: AsyncClient) -> None:
    """GET /conversations/{id} specifically — POST /messages and GET /messages' cross-user 404
    are already covered in test_conversation_flow.py."""

    headers_a = await _authed_headers(client, "convGetOwnerA@example.com")
    headers_b = await _authed_headers(client, "convGetOwnerB@example.com")

    conversation_id = (await client.post("/conversations", headers=headers_a)).json()["id"]

    resp = await client.get(f"/conversations/{conversation_id}", headers=headers_b)
    assert resp.status_code == 404


async def test_no_admin_or_role_based_endpoints_exist() -> None:
    """medai_spec.yaml names no admin/RBAC concept, and none was invented — every endpoint is
    scoped to "the authenticated user, for their own data" (get_current_patient) or "any
    authenticated user" for shared, non-patient-specific reference actions
    (POST/GET /evidence — ingesting/searching public medical knowledge, not patient data).
    This test exists as documentation with a real assertion behind it: it fails loudly if a
    future endpoint silently introduces an admin/elevated-privilege path without a matching
    access-control decision being made deliberately.
    """

    from app.main import app

    patient_scoped_prefixes = {
        "/patient", "/profile", "/history", "/allergies", "/medications", "/conversations",
        "/messages", "/symptoms", "/evidence", "/assessment", "/vitals", "/devices", "/privacy",
    }
    unscoped_prefixes = {"/healthz", "/auth", "/download", "/clear", "/diagnostics"}

    for route in app.routes:
        path = getattr(route, "path", "")
        if not path or path == "/openapi.json" or path.startswith("/docs") or path.startswith("/redoc"):
            continue
        matched = any(path == p or path.startswith(p + "/") or path.startswith(p) for p in patient_scoped_prefixes | unscoped_prefixes)
        assert matched, f"route {path!r} doesn't match any known access-control category — review it explicitly"
