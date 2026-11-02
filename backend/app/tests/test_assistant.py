"""The assistant end to end, on the sales_org fixture (see conftest for the hand-worked numbers)."""

from sqlalchemy import select

from app.ai.llm_provider import LLMProvider
from app.ai.nlu import parse
from app.ai.orchestrator import answer
from app.models import Conversation, User
from app.tests.conftest import auth_headers


def ask(client, headers, text, conversation_id=None):
    response = client.post("/api/v1/assistant/messages", headers=headers, json={"text": text, "conversation_id": conversation_id})
    assert response.status_code == 200, response.text
    body = response.json()
    return body["conversation_id"], body["message"]


def test_sales_question_and_follow_up(client, sales_org):
    admin = sales_org["admin"]
    conv, msg = ask(client, admin, "What were our sales in October 2026?")
    assert msg["intent"] == "sales_performance"
    assert "Revenue: *₹9,000*" in msg["content"]
    assert "3 orders" in msg["content"]
    assert msg["based_on"]["period"] == ["2026-10-01", "2026-10-31"]
    assert (msg["based_on"]["provider"], msg["based_on"]["grounded"]) == ("rule-based", True)

    _, msg = ask(client, admin, "compare with September 2026", conv)
    assert msg["intent"] == "compare_periods"
    assert "₹9,000" in msg["content"] and "₹2,000" in msg["content"] and "+350.0%" in msg["content"]

    messages = client.get(f"/api/v1/assistant/conversations/{conv}", headers=admin).json()
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]


def test_rankings_and_customer(client, sales_org):
    admin = sales_org["admin"]
    _, msg = ask(client, admin, "Top 2 customers in October 2026")
    assert "1. Mehta Mart: *₹5,000*" in msg["content"]
    assert "2. Gupta Stores: *₹4,000*" in msg["content"]

    _, msg = ask(client, admin, "how much did Gupta Stores buy in October 2026")
    assert msg["intent"] == "customer_summary"
    assert "Bought: *₹4,000* in 2 orders" in msg["content"]


def test_target_for_a_past_month(client, sales_org):
    ravi = auth_headers(client, "ravi@acme.in")
    _, msg = ask(client, ravi, "what was my target in October 2026")
    assert "Target: ₹8,000" in msg["content"]
    assert "Achieved: *₹4,000* (50.0%)" in msg["content"]
    assert "At the current pace" not in msg["content"]  # the month is over: no projection


def test_scope_is_enforced(client, sales_org):
    ravi = auth_headers(client, "ravi@acme.in")
    _, msg = ask(client, ravi, "how is Sita doing in October 2026?")
    assert "You can only see numbers for yourself" in msg["content"]
    assert "5,000" not in msg["content"]

    _, msg = ask(client, ravi, "my sales in October 2026")
    assert "Revenue: *₹4,000*" in msg["content"]

    # Sita's customer isn't even recognisable for Ravi.
    _, msg = ask(client, ravi, "how much did Mehta Mart buy in October 2026")
    assert "5,000" not in msg["content"]


def test_unknown_questions_are_not_answered(client, sales_org):
    _, msg = ask(client, sales_org["admin"], "what's the weather in Pune tomorrow?")
    assert msg["intent"] == "unknown"
    assert "can't answer" in msg["content"]


def test_conversations_are_private(client, sales_org):
    ravi = auth_headers(client, "ravi@acme.in")
    sita = auth_headers(client, "sita@acme.in")
    conv, _ = ask(client, ravi, "my sales in October 2026")
    assert client.get(f"/api/v1/assistant/conversations/{conv}", headers=sita).status_code == 404
    assert client.get(f"/api/v1/assistant/conversations/{conv}", headers=sales_org["admin"]).status_code == 404
    response = client.post("/api/v1/assistant/messages", headers=sita, json={"text": "hi", "conversation_id": conv})
    assert response.status_code == 404
    assert [c["id"] for c in client.get("/api/v1/assistant/conversations", headers=ravi).json()] == [conv]


class FakeModel(LLMProvider):
    """Stands in for a language model: understands via the rules, then 'rewrites' the answer."""

    name = "fake-llm"
    uses_model = True

    def __init__(self, rewrite):
        self.rewrite = rewrite

    def interpret(self, message, previous, catalog, today):
        return parse(message, catalog)

    def phrase(self, message, facts, draft, language):
        return self.rewrite


def test_model_replies_with_invented_numbers_are_discarded(db_session, sales_org):
    admin = db_session.scalar(select(User).where(User.email == "admin@acme.in"))
    conv = Conversation(organization_id=admin.organization_id, user_id=admin.id, channel="web")

    good = answer(db_session, admin, conv, "sales in October 2026", FakeModel("You sold ₹9,000 across 3 orders in Oct 2026."))
    assert (good.text, good.grounded) == ("You sold ₹9,000 across 3 orders in Oct 2026.", True)

    bad = answer(db_session, admin, conv, "sales in October 2026", FakeModel("You sold ₹12.5 L, up 40%!"))
    assert bad.grounded is False
    assert "Revenue: *₹9,000*" in bad.text  # fell back to the template built from the facts
