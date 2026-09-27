"""AI endpoints. Flow (PRD §54):

authenticate → load data → Rules Engine → (emergency? bypass AI) → minimal context
→ system prompt → Grok → validate → respond (fallback if anything fails).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import func, select

from app.ai.context_builder import build_ai_context, detect_topics
from app.ai.grok_client import ChatClient, get_ai_client
from app.ai.safety import detect_language, screen_user_message
from app.ai.service import chat_reply, deterministic_summary, emergency_message, questions_for_doctor, summary_reply
from app.api.deps import DB, CurrentUser, get_owned
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.domain import Severity
from app.models import AIConversation, AIMessage
from app.schemas.medical_ai import (
    ChatIn,
    ChatOut,
    ConversationDetail,
    ConversationOut,
    SafetyAlert,
    SummaryIn,
    SummaryOut,
)
from app.services.findings_service import evaluate_and_sync
from app.services.medical_rules import Evaluation, MedicalRulesEngine, load_content

router = APIRouter(prefix="/ai", tags=["ai"])
AI = Annotated[ChatClient, Depends(get_ai_client)]


def _rate_limit(user_id: uuid.UUID) -> None:
    s = get_settings()
    limiter.check(f"ai:{user_id}", s.ai_rate_limit, s.ai_rate_window_seconds)


def _safety_alert(evaluation: Evaluation, language: str) -> SafetyAlert | None:
    a = evaluation.alert
    if a is None or a.severity < Severity.URGENT:
        return None
    return SafetyAlert(severity=a.severity.value, title=a.title, body=f"{a.summary}\n\n{a.recommended_action}", is_emergency=a.is_emergency)


def _generic_emergency_alert(language: str) -> SafetyAlert:
    e = load_content()["emergency"]["generic"][language]
    return SafetyAlert(severity=Severity.EMERGENCY.value, title=e["title"], body=e["body"], is_emergency=True)


@router.post("/chat", response_model=ChatOut)
def chat(body: ChatIn, user: CurrentUser, db: DB, client: AI):
    _rate_limit(user.id)
    language = detect_language(body.message, default=user.language or "ar")

    if body.conversation_id:
        conv = get_owned(db, AIConversation, body.conversation_id, user)
    else:
        conv = AIConversation(user_id=user.id, title=body.message[:60])
        db.add(conv)
        db.flush()
    history = [{"role": m.role, "content": m.content} for m in conv.messages if m.role in ("user", "assistant")]
    conv.updated_at = func.now()
    db.add(AIMessage(conversation_id=conv.id, role="user", content=body.message))

    # Rules Engine ALWAYS runs first; findings are rendered in the conversation language.
    data, _ = evaluate_and_sync(db, user)
    evaluation = MedicalRulesEngine(language=language).evaluate(data.rules_input())
    safety = _safety_alert(evaluation, language)
    screen = screen_user_message(body.message)
    topics = sorted(detect_topics(body.message))

    if screen.is_emergency or evaluation.overall_severity == Severity.EMERGENCY:
        # Emergencies bypass the LLM entirely (PRD P4, §49).
        reply = emergency_message(language, evaluation, screen.category or "physical")
        safety = safety or _generic_emergency_alert(language)
        db.add(AIMessage(conversation_id=conv.id, role="assistant", content=reply, model=None))
        db.commit()
        return ChatOut(
            conversation_id=conv.id,
            reply=reply,
            language=language,
            ai_generated=False,
            model=None,
            fallback_reason="self_harm" if screen.category == "self_harm" else "emergency",
            safety_alert=safety,
            overall_severity=evaluation.overall_severity.value,
            context_topics=topics,
        )

    context = build_ai_context(data, evaluation, data.predict(), message=body.message)
    result = chat_reply(
        client,
        context=context,
        history=history,
        user_message=body.message,
        language=language,
        pregnancy_confirmed=evaluation.pregnancy_confirmed,
    )
    db.add(AIMessage(conversation_id=conv.id, role="assistant", content=result.text, model=result.model))
    db.commit()
    return ChatOut(
        conversation_id=conv.id,
        reply=result.text,
        language=language,
        ai_generated=result.ai_generated,
        model=result.model,
        fallback_reason=result.fallback_reason,
        safety_alert=safety,
        overall_severity=evaluation.overall_severity.value,
        context_topics=context["included_topics"],
    )


@router.post("/summary", response_model=SummaryOut)
def summary(body: SummaryIn, user: CurrentUser, db: DB, client: AI):
    _rate_limit(user.id)
    language = user.language if user.language in ("ar", "en") else "ar"
    cycles = int(body.period.split("_")[0])
    data, evaluation = evaluate_and_sync(db, user)
    db.commit()
    safety = _safety_alert(evaluation, language)
    findings = [f.to_dict() for f in evaluation.findings]
    questions = questions_for_doctor(evaluation, data, language)

    if evaluation.overall_severity == Severity.EMERGENCY:
        return SummaryOut(
            summary=emergency_message(language, evaluation),
            ai_generated=False,
            model=None,
            fallback_reason="emergency",
            findings=findings,
            questions_for_doctor=questions,
            safety_alert=safety,
            overall_severity=evaluation.overall_severity.value,
            language=language,
        )

    context = build_ai_context(data, evaluation, data.predict(), cycles=cycles, topics={"cycle", "bleeding", "symptoms"})
    result = summary_reply(client, context=context, language=language, pregnancy_confirmed=evaluation.pregnancy_confirmed)
    text = result.text if result.ai_generated else deterministic_summary(data, evaluation, cycles, language)
    return SummaryOut(
        summary=text,
        ai_generated=result.ai_generated,
        model=result.model,
        fallback_reason=result.fallback_reason,
        findings=findings,
        questions_for_doctor=questions,
        safety_alert=safety,
        overall_severity=evaluation.overall_severity.value,
        language=language,
    )


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(user: CurrentUser, db: DB):
    return db.scalars(select(AIConversation).where(AIConversation.user_id == user.id).order_by(AIConversation.updated_at.desc())).all()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: uuid.UUID, user: CurrentUser, db: DB):
    return get_owned(db, AIConversation, conversation_id, user)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: uuid.UUID, user: CurrentUser, db: DB) -> Response:
    conv = get_owned(db, AIConversation, conversation_id, user)
    db.delete(conv)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
