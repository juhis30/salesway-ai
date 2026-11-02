"""Language-model providers. The model understands and phrases; it never calculates.

* MockProvider: rule-based understanding (app.ai.nlu) + template replies. No key needed.
* AnthropicProvider: Claude reads the question into the same Interpretation schema (structured
  output), and rewrites the template answer in the user's language and tone. Its reply is then
  checked against the facts (app.ai.guardrails) by the orchestrator.

Add another provider (Bedrock, Sarvam, a local Llama) by implementing the same two methods.
"""

import json
import logging
from datetime import date
from functools import lru_cache

from app.ai import nlu
from app.ai.catalog import Catalog
from app.ai.schema import INTENT_DESCRIPTIONS, Facts, Interpretation, to_jsonable
from app.core.config import get_settings

log = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """The provider couldn't produce a usable result; the caller falls back to the rule-based path."""


class LLMProvider:
    name = "base"
    uses_model = False

    def interpret(self, message: str, previous: dict | None, catalog: Catalog, today: date) -> Interpretation:
        raise NotImplementedError

    def phrase(self, message: str, facts: Facts, draft: str, language: str) -> str:
        raise NotImplementedError


class MockProvider(LLMProvider):
    name = "rule-based"

    def interpret(self, message, previous, catalog, today):
        return nlu.parse(message, catalog)

    def phrase(self, message, facts, draft, language):
        return draft


INTERPRET_SYSTEM = """You turn a sales team member's question into a structured query for SaleSway, an Indian sales analytics app.
You never answer the question or calculate anything: SaleSway's analytics engine does that from the query you produce.

Intents:
{intents}

Rules:
- Pick the single best intent. Use "unknown" for anything SaleSway's sales data can't answer (weather, inventory, HR, general knowledge).
- Periods: use the relative kinds (this_month, last_month, last_n_days, ...) whenever the user speaks relatively. For a named month use kind=month with month (1-12) and year only if they said one. Hindi/Hinglish: "is mahine" = this month, "pichle mahine" = last month, "aaj" = today, "kal" (about sales) = yesterday.
- compare_to is the second period of a comparison ("this month vs last month" -> period this_month, compare_to last_month). "Compare with last month" alone -> compare_to last_month, no period, follows_previous=true.
- Copy customer, product, salesperson and territory names exactly as the user wrote them; leave them empty when not mentioned. "My"/"I" is not a salesperson name.
- follows_previous=true when the message only makes sense with the previous question ("and last month?", "what about Pune?", "that customer").
- language="hi" if the user writes in Hindi or Hinglish, otherwise "en".
"""

PHRASE_SYSTEM = """You are SaleSway, a sales assistant replying on WhatsApp to a sales team member in India.
You are given the user's question, the FACTS computed by SaleSway's analytics engine, and a DRAFT answer built from those facts.

Rewrite the draft so it reads naturally and helpfully, in the user's language ({language_note}).
Hard rules:
- Use only numbers that appear in the facts or the draft. Never calculate, estimate, round differently or add new figures.
- Keep money in Indian format as in the draft (₹ with lakh "L" and crore "Cr").
- Keep it short: WhatsApp style, *bold* for key figures, "•" bullets, under 120 words.
- Keep the caveats and the data-freshness line from the draft.
- If the facts say there is no data, say so plainly. Never guess.
"""


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    uses_model = True
    # Server-side fallback: if the model declines a request, the API retries on a suitable model.
    BETAS = ["server-side-fallback-2026-07-01"]

    def __init__(self, model: str):
        import anthropic  # imported lazily so the mock path has no dependency on it

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=2, timeout=30.0)
        self.model = model
        self.interpret_system = INTERPRET_SYSTEM.format(
            intents="\n".join(f"- {i.value}: {d}" for i, d in INTENT_DESCRIPTIONS.items())
        )

    def _call(self, fn, **kwargs):
        try:
            response = fn(model=self.model, betas=self.BETAS, fallbacks="default", **kwargs)
        except self._anthropic.APIConnectionError as exc:
            raise ProviderError(f"connection: {exc}") from exc
        except self._anthropic.RateLimitError as exc:
            raise ProviderError("rate limited") from exc
        except self._anthropic.APIStatusError as exc:
            raise ProviderError(f"API error {exc.status_code}") from exc
        if response.stop_reason == "refusal":
            raise ProviderError("refused")
        if response.stop_reason == "max_tokens":
            raise ProviderError("ran out of tokens")
        return response

    def interpret(self, message, previous, catalog, today):
        context = {
            "today": today.isoformat(),
            "previous_question": previous,
            "salespeople_user_can_see": [p.name for p in catalog.people][:50],
            "territories": [t.name for t in catalog.territories],
        }
        response = self._call(
            self.client.beta.messages.parse,
            max_tokens=4000,
            system=self.interpret_system,
            messages=[{"role": "user", "content": f"Context: {json.dumps(context)}\n\nQuestion: {message}"}],
            output_format=Interpretation,
        )
        if response.parsed_output is None:
            raise ProviderError("no structured output")
        return response.parsed_output

    def phrase(self, message, facts, draft, language):
        language_note = "Hinglish (Hindi in Latin script), as the user wrote" if language == "hi" else "English"
        response = self._call(
            self.client.beta.messages.create,
            max_tokens=4000,
            output_config={"effort": "low"},
            system=PHRASE_SYSTEM.format(language_note=language_note),
            messages=[{
                "role": "user",
                "content": f"Question: {message}\n\nFACTS:\n{json.dumps(to_jsonable(facts), ensure_ascii=False)}\n\nDRAFT:\n{draft}",
            }],
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise ProviderError("empty reply")
        return text


@lru_cache
def get_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider == "anthropic":
        try:
            return AnthropicProvider(settings.anthropic_model)
        except Exception:  # missing package or credentials: keep the assistant working
            log.exception("Could not start the Anthropic provider; using the rule-based assistant")
    return MockProvider()
