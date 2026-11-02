import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.orchestrator import answer, record_exchange
from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.ratelimit import RateLimiter
from app.models import Conversation, Message, User

router = APIRouter(prefix="/assistant", tags=["assistant"])
limiter = RateLimiter(get_settings().assistant_rate_limit_per_minute)


class AskIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    conversation_id: uuid.UUID | None = None


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    intent: str | None
    created_at: datetime
    # For assistant messages: what the answer was based on.
    based_on: dict | None = None
    suggestions: list[str] = []


class AskOut(BaseModel):
    conversation_id: uuid.UUID
    message: MessageOut


class ConversationOut(BaseModel):
    id: uuid.UUID
    channel: str
    title: str | None
    last_message_at: datetime


def _message_out(m: Message) -> MessageOut:
    meta = m.meta or {}
    facts = meta.get("facts") or {}
    based_on = None
    if m.role == "assistant" and facts:
        based_on = {
            "intent": m.intent,
            "scope": facts.get("scope"),
            "period": facts.get("period"),
            "comparison": facts.get("comparison"),
            "data_as_of": facts.get("data_as_of"),
            "provider": meta.get("provider"),
            "grounded": meta.get("grounded", True),
        }
    return MessageOut(
        id=m.id, role=m.role, content=m.content, intent=m.intent, created_at=m.created_at,
        based_on=based_on, suggestions=meta.get("suggestions", []),
    )


def _own_conversation(db: Session, user: User, conversation_id: uuid.UUID) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    # Conversations are private to their user, even from admins.
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


@router.post("/messages", response_model=AskOut)
def ask(body: AskIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Ask a question. The answer is computed from your data, within what your role may see."""
    limiter.check(str(user.id))
    if body.conversation_id:
        conversation = _own_conversation(db, user, body.conversation_id)
    else:
        conversation = Conversation(organization_id=user.organization_id, user_id=user.id, channel="web")
        db.add(conversation)
        db.flush()

    reply = answer(db, user, conversation, body.text.strip())
    message = record_exchange(db, user, conversation, body.text.strip(), reply)
    db.commit()
    return AskOut(conversation_id=conversation.id, message=_message_out(message))


@router.get("/conversations", response_model=list[ConversationOut])
def conversations(
    limit: int = Query(default=30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.last_message_at.desc())
        .limit(limit)
    ).all()


@router.get("/conversations/{conversation_id}", response_model=list[MessageOut])
def conversation_messages(conversation_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conversation = _own_conversation(db, user, conversation_id)
    messages = db.scalars(
        select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at)
    ).all()
    return [_message_out(m) for m in messages]
