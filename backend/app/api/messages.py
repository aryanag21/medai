import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_patient, get_owned_conversation
from app.conversation.manager import generate_reply
from app.db.models import Allergy, Conversation, CurrentMedication, MedicalHistory, Message, Patient, PatientProfile, Symptom
from app.db.session import get_db
from app.patient_state.assembler import build_patient_state
from app.providers.embeddings import EmbeddingProvider, get_embedding_provider
from app.providers.llm import LLMProvider, get_llm_provider
from app.providers.vector_store import VectorStore, get_vector_store
from app.reasoning.evidence_package import build_evidence_package
from app.safety.engine import evaluate_safety, vitals_from_patient_state
from app.schemas.conversation import MessageCreateRequest, MessageExchangeResponse, MessageResponse
from app.validation.validator import get_validated_output

router = APIRouter(prefix="/messages", tags=["messages"])


@router.post("", response_model=MessageExchangeResponse, status_code=status.HTTP_201_CREATED)
async def send_message(
    payload: MessageCreateRequest,
    patient: Patient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
    llm: LLMProvider = Depends(get_llm_provider),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
    vector_store: VectorStore = Depends(get_vector_store),
) -> MessageExchangeResponse:
    """Stores the user's message, then runs the Phase 4 conversation manager
    (app/conversation/manager.py) to decide the next action: either the highest-priority
    follow-up question, or — once nothing more is missing — the real RUN_ASSESSMENT pipeline
    (Phase 12: architecture.canonical_pipeline's EVIDENCE_RETRIEVAL through OUTPUT_VALIDATION,
    the same steps POST /assessment runs, reachable here automatically instead of only via a
    separate manual action). No medical reasoning happens in the ASK_QUESTION path
    (phases.2_conversation.constraint, carried into Phase 4)."""

    conversation = await get_owned_conversation(db, patient, payload.conversation_id)

    # 1. Fetch prior messages to check what the assistant previously asked
    prior_messages = (
        await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at)
        )
    ).scalars().all()

    assistant_history = [m.content for m in prior_messages if m.role == "assistant"]
    last_question = assistant_history[-1].lower() if assistant_history else ""

    content_cleaned = payload.content.strip()
    content_lower = content_cleaned.lower()

    # Chat clearing / new conversation trigger without requiring mobile app updates
    clear_keywords = {"clear", "clear chat", "/clear", "reset", "start over", "new chat", "new conversation", "restart"}
    if content_lower in clear_keywords:
        from sqlalchemy import delete
        await db.execute(delete(Message).where(Message.conversation_id == conversation.id))
        await db.execute(delete(Symptom).where(Symptom.conversation_id == conversation.id))

        new_conv = Conversation(patient_id=patient.id)
        db.add(new_conv)
        await db.flush()

        user_msg = Message(conversation_id=conversation.id, role="user", content=payload.content)
        fresh_msg = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="✨ Chat cleared! I'm ready for a fresh consultation. (To see a clean empty screen, you can also back out to Home and tap 'Talk to Health AI'). What symptoms or questions would you like to discuss today?",
        )
        db.add(user_msg)
        db.add(fresh_msg)
        await db.commit()
        await db.refresh(user_msg)
        await db.refresh(fresh_msg)

        return MessageExchangeResponse(
            user_message=user_msg,
            assistant_message=fresh_msg,
            is_assessment=False,
            assessment_status=None,
            escalation=None,
        )

    negative_words = {
        "no", "none", "no allergies", "nil", "n/a", "na", "nothing", "nope",
        "i have no allergies", "i don't have any", "i dont have any", "never",
        "not that i know of", "zero", "no medications", "no history", "no conditions",
    }
    is_negative = content_lower in negative_words or any(
        content_lower.startswith(w)
        for w in ["none", "no allergies", "no medications", "no history", "no conditions", "i don't have", "i dont have", "nothing"]
    )

    is_symptom_statement = any(
        w in content_lower
        for w in ["cough", "cold", "fever", "pain", "headache", "throat", "nausea", "dizzy", "breathe", "breathing", "hurts", "ache", "chills", "feeling", "sick"]
    )

    # If the previous question asked about allergies
    if "allerg" in last_question and not is_symptom_statement:
        if not is_negative:
            substance = content_cleaned
            if "allergic to" in substance.lower():
                substance = substance.lower().split("allergic to")[-1].strip(". ,")
            if substance and len(substance) < 80:
                db.add(Allergy(patient_id=patient.id, substance=substance, severity="user_reported"))

    # If the previous question asked about medications
    elif ("medicat" in last_question or "medicine" in last_question or "taking" in last_question) and not is_symptom_statement:
        if not is_negative and len(content_cleaned) < 80:
            db.add(CurrentMedication(patient_id=patient.id, name=content_cleaned))

    # If the previous question asked about medical history or conditions
    elif ("history" in last_question or "condition" in last_question or "ongoing" in last_question) and not is_symptom_statement:
        if not is_negative and len(content_cleaned) < 120:
            db.add(MedicalHistory(patient_id=patient.id, condition=content_cleaned, status="active"))

    # Update profile fields if user answered demographic questions
    profile = await db.get(PatientProfile, patient.id)
    if profile is None:
        profile = PatientProfile(patient_id=patient.id)
        db.add(profile)

    if "age" in last_question or "how old" in last_question:
        import re
        match = re.search(r"\b(\d{1,3})\b", content_cleaned)
        if match:
            profile.age = int(match.group(1))
    elif "sex" in last_question or "gender" in last_question:
        if "male" in content_lower and "female" not in content_lower:
            profile.sex = "male"
        elif "female" in content_lower:
            profile.sex = "female"
        elif content_lower in ["other", "non-binary"]:
            profile.sex = content_cleaned
    elif "height" in last_question:
        import re
        match = re.search(r"(\d+(\.\d+)?)", content_cleaned)
        if match:
            profile.height_cm = float(match.group(1))
    elif "weight" in last_question:
        import re
        match = re.search(r"(\d+(\.\d+)?)", content_cleaned)
        if match:
            profile.weight_kg = float(match.group(1))

    user_message = Message(conversation_id=conversation.id, role="user", content=payload.content)
    db.add(user_message)
    await db.flush()

    # Extract symptoms on any turn to keep clinical state up to date
    try:
        from app.patient_state.extraction import extract_symptoms_from_conversation
        extracted = await extract_symptoms_from_conversation(llm, [payload.content])
        for s in extracted:
            existing = await db.execute(
                select(Symptom).where(
                    Symptom.patient_id == patient.id,
                    Symptom.symptom == s.symptom,
                )
            )
            if not existing.scalar_one_or_none():
                db.add(Symptom(patient_id=patient.id, conversation_id=conversation.id, **s.model_dump()))
        await db.flush()
    except Exception:
        pass

    state = await build_patient_state(db, patient)
    history = [{"role": m.role, "content": m.content} for m in prior_messages]

    from app.conversation.clinical_engine import generate_conversational_reply
    reply_text, is_assessment, assessment_status, escalation = await generate_conversational_reply(
        llm=llm,
        state=state,
        latest_message=payload.content,
        conversation_history=history,
    )

    assistant_message = Message(conversation_id=conversation.id, role="assistant", content=reply_text)
    db.add(assistant_message)
    await db.commit()
    await db.refresh(user_message)
    await db.refresh(assistant_message)

    return MessageExchangeResponse(
        user_message=user_message,
        assistant_message=assistant_message,
        is_assessment=is_assessment,
        assessment_status=assessment_status,
        escalation=escalation,
    )


@router.get("", response_model=list[MessageResponse])
async def list_messages(
    conversation_id: uuid.UUID = Query(...),
    patient: Patient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
) -> list[Message]:
    await get_owned_conversation(db, patient, conversation_id)
    result = await db.execute(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
    )
    return list(result.scalars().all())
