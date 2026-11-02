"""One question in, one grounded answer out. Shared by the web chat and WhatsApp."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.ai.catalog import load_catalog
from app.ai.executor import STARTERS, Executor, NeedsClarification
from app.ai.guardrails import ungrounded_numbers
from app.ai.llm_provider import LLMProvider, MockProvider, ProviderError, get_provider
from app.ai.nlu import merge_with_previous, parse
from app.ai.responder import render
from app.ai.schema import Facts, Intent, Interpretation, to_jsonable
from app.analytics.periods import org_today
from app.models import Conversation, Message, Organization, User
from app.services import audit

log = logging.getLogger(__name__)

CONTEXT_TTL = timedelta(minutes=30)  # after a pause, a new question starts fresh
NO_CONTEXT = {Intent.GREETING, Intent.HELP, Intent.UNKNOWN}


@dataclass
class Reply:
    text: str
    intent: Intent
    interpretation: Interpretation
    facts: Facts | None
    provider: str
    grounded: bool = True
    suggestions: list[str] = field(default_factory=list)


def _recent(conversation: Conversation) -> bool:
    last = conversation.last_message_at
    if last is None:
        return False
    if last.tzinfo is None:  # SQLite returns naive datetimes
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last < CONTEXT_TTL


def answer(db: Session, user: User, conversation: Conversation, message: str, provider: LLMProvider | None = None) -> Reply:
    org = db.get(Organization, user.organization_id)
    today = org_today(org.timezone)
    catalog = load_catalog(db, user)
    previous = (conversation.state or {}).get("last") if _recent(conversation) else None
    provider = provider or get_provider()
    used = provider.name

    try:
        interp = provider.interpret(message, previous, catalog, today)
    except Exception as exc:  # the rule-based path always works
        log.warning("interpret failed with %s (%s); using rules", provider.name, exc)
        interp, used = parse(message, catalog), f"{MockProvider.name} (fallback)"
    interp = merge_with_previous(interp, previous)

    try:
        facts = Executor(db, user, org, catalog, today).run(interp)
    except NeedsClarification as c:
        facts = c.facts
    except HTTPException as exc:
        title = ("You can only see numbers for yourself and the people you manage."
                 if exc.status_code == 403 else "I couldn't find that person in your organisation.")
        facts = Facts(intent=interp.intent, title=title, scope="", clarify=True)

    draft = render(facts)
    text, grounded = draft, True
    if provider.uses_model and not facts.clarify and facts.intent not in NO_CONTEXT and not used.endswith("(fallback)"):
        try:
            candidate = provider.phrase(message, facts, draft, interp.language)
            bad = ungrounded_numbers(candidate, facts)
            if bad:
                grounded = False
                log.warning("Discarded model reply with ungrounded numbers %s", bad)
            else:
                text = candidate
        except ProviderError as exc:
            log.warning("phrase failed (%s); using template", exc)

    if facts.intent not in NO_CONTEXT and not facts.clarify:
        conversation.state = {"last": interp.model_dump(mode="json", exclude_none=True)}
    return Reply(
        text=text,
        intent=facts.intent,
        interpretation=interp,
        facts=facts,
        provider=used,
        grounded=grounded,
        suggestions=facts.suggestions or (STARTERS[:4] if facts.intent in NO_CONTEXT else []),
    )


def record_exchange(
    db: Session,
    user: User,
    conversation: Conversation,
    question: str,
    reply: Reply,
    inbound_id: str | None = None,
) -> Message:
    """Store the question and answer (with what the answer was based on) and audit it."""
    now = datetime.now(timezone.utc)
    db.add(Message(organization_id=user.organization_id, conversation_id=conversation.id, role="user",
                   content=question, channel_message_id=inbound_id, created_at=now))
    assistant = Message(
        organization_id=user.organization_id,
        conversation_id=conversation.id,
        role="assistant",
        content=reply.text,
        intent=reply.intent.value,
        meta={
            "interpretation": reply.interpretation.model_dump(mode="json", exclude_none=True),
            "facts": to_jsonable(reply.facts) if reply.facts else None,
            "provider": reply.provider,
            "grounded": reply.grounded,
            "suggestions": reply.suggestions,
        },
        created_at=now + timedelta(microseconds=1),
    )
    db.add(assistant)
    conversation.last_message_at = now
    if not conversation.title:
        conversation.title = question[:120]
    audit.record(db, user.organization_id, user.id, "assistant.answered", "conversation", conversation.id,
                 {"intent": reply.intent.value, "channel": conversation.channel, "provider": reply.provider,
                  "grounded": reply.grounded})
    db.flush()
    return assistant
