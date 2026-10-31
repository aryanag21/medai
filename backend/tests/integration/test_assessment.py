import pytest
from httpx import AsyncClient

from app.db.models import Symptom
from app.main import app
from app.providers.embeddings import get_embedding_provider
from app.providers.llm import get_llm_provider
from app.providers.medical_knowledge import get_medical_knowledge_provider
from app.rag.ingest import ingest_topic
from tests.fakes import FakeEmbeddingProvider, FakeMedicalKnowledgeProvider, FakeReasoningLLMProvider, NotConfiguredLLMProvider

pytestmark = pytest.mark.asyncio


async def _authed_headers(client: AsyncClient, email: str) -> dict[str, str]:
    resp = await client.post("/auth/register", json={"email": email, "password": "s3curePassw0rd"})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _conversation_with_symptom(client: AsyncClient, headers: dict[str, str], db_session) -> str:
    from sqlalchemy import select

    from app.db.models import Patient, User

    conversation_id = (await client.post("/conversations", headers=headers)).json()["id"]
    await client.post(
        "/messages", headers=headers, json={"conversation_id": conversation_id, "content": "I have a headache"}
    )

    # Insert a symptom directly (bypassing a real extraction call) so the evidence-retrieval
    # query in build_evidence_package has something to work with.
    patient_resp = await client.get("/patient", headers=headers)
    patient_id = patient_resp.json()["id"]
    db_session.add(Symptom(patient_id=patient_id, symptom="headache", severity=6, certainty="user_reported"))
    await db_session.commit()

    return conversation_id


async def test_assessment_requires_auth(client: AsyncClient) -> None:
    resp = await client.post("/assessment", json={"conversation_id": "00000000-0000-0000-0000-000000000000"})
    assert resp.status_code in (401, 403)


async def test_assessment_404_for_unowned_conversation(client: AsyncClient) -> None:
    headers_a = await _authed_headers(client, "assessA@example.com")
    headers_b = await _authed_headers(client, "assessB@example.com")
    conversation_id = (await client.post("/conversations", headers=headers_a)).json()["id"]

    resp = await client.post("/assessment", headers=headers_b, json={"conversation_id": conversation_id})
    assert resp.status_code == 404


async def test_assessment_returns_validated_grounded_assessment(client: AsyncClient, db_session) -> None:
    headers = await _authed_headers(client, "assessC@example.com")
    conversation_id = await _conversation_with_symptom(client, headers, db_session)

    embeddings = FakeEmbeddingProvider()
    app.dependency_overrides[get_medical_knowledge_provider] = lambda: FakeMedicalKnowledgeProvider()
    app.dependency_overrides[get_embedding_provider] = lambda: embeddings
    try:
        sources = await ingest_topic(db_session, FakeMedicalKnowledgeProvider(), embeddings, "headache")
        source_id = sources[0].id

        from app.reasoning.schema import Assessment, EvidenceReference

        good_assessment = Assessment(
            status="caution",
            summary="Possible explanations include a tension headache.",
            confidence="low",
            evidence=[EvidenceReference(source_id=source_id, note="supports the summary")],
        )
        app.dependency_overrides[get_llm_provider] = lambda: FakeReasoningLLMProvider([good_assessment])

        resp = await client.post("/assessment", headers=headers, json={"conversation_id": conversation_id})
    finally:
        del app.dependency_overrides[get_medical_knowledge_provider]
        del app.dependency_overrides[get_embedding_provider]
        del app.dependency_overrides[get_llm_provider]

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "caution"
    assert body["evidence"][0]["source_id"] == str(source_id)


async def test_assessment_rejects_llm_output_that_ignores_a_real_escalating_vital(
    client: AsyncClient, db_session
) -> None:
    """Phase 9 integration: a real ingested critical vital must drive the endpoint's safety
    evaluation to ESCALATE, and Phase 8's check_safety_engine_result must then reject an LLM
    assessment that doesn't reflect it — exercising the correction pipeline for real, through
    the full HTTP path, with a real (dangerously low) vital on record."""

    headers = await _authed_headers(client, "assessVitals@example.com")
    conversation_id = await _conversation_with_symptom(client, headers, db_session)
    await client.post("/vitals", headers=headers, json={"type": "oxygen_saturation", "value": 85, "unit": "%"})

    from app.reasoning.schema import Assessment

    ignores_escalation = Assessment(status="caution", summary="Seems mild.", confidence="low")
    correct = Assessment(
        status="emergency",
        summary="This requires urgent attention given the low oxygen saturation.",
        confidence="low",
        escalation="Seek emergency care immediately.",
    )
    app.dependency_overrides[get_llm_provider] = lambda: FakeReasoningLLMProvider([ignores_escalation, correct])
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider()
    try:
        resp = await client.post("/assessment", headers=headers, json={"conversation_id": conversation_id})
    finally:
        del app.dependency_overrides[get_llm_provider]
        del app.dependency_overrides[get_embedding_provider]

    assert resp.status_code == 200
    assert resp.json()["status"] == "emergency"
    assert resp.json()["escalation"]


async def test_assessment_falls_back_when_llm_not_configured(client: AsyncClient, db_session) -> None:
    headers = await _authed_headers(client, "assessD@example.com")
    conversation_id = await _conversation_with_symptom(client, headers, db_session)

    app.dependency_overrides[get_llm_provider] = lambda: NotConfiguredLLMProvider()
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider()
    try:
        resp = await client.post("/assessment", headers=headers, json={"conversation_id": conversation_id})
    finally:
        del app.dependency_overrides[get_llm_provider]
        del app.dependency_overrides[get_embedding_provider]

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] == "Insufficient information to provide a reliable assessment."
    assert body["confidence"] == "low"
