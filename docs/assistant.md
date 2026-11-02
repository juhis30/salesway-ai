# The SaleSway assistant

Ask questions in plain English or Hinglish on the web ("Ask SaleSway") or WhatsApp, and get
answers computed from your own data, within what your role is allowed to see.

## How an answer is made

```
question ─► interpret ─► merge with previous question ─► execute ─► write reply ─► grounding check ─► send
            (rules or     ("and last month?")              (analytics    (template, or   (model reply
             Claude)                                        engine only)  Claude rewrite)  discarded if it
                                                                                           has new numbers)
```

| Step | Code | What it guarantees |
| ---- | ---- | ------------------ |
| Interpret | `app/ai/nlu.py` (rules), `app/ai/llm_provider.py` (Claude) | The question becomes a fixed schema (`Interpretation`): one of 15 intents, periods, names. Nothing else can reach the database. |
| Resolve names | `app/ai/catalog.py` | Only customers the user can see are recognisable. Ambiguous names ("atta") produce "which one did you mean?" instead of a guess. |
| Execute | `app/ai/executor.py` | Calls the same `app/analytics` functions as the dashboard. Scope is enforced again here: asking about a colleague outside your scope is refused. No SQL is ever written by a model. |
| Reply | `app/ai/responder.py` | Template answer from the facts only. With Claude enabled, Claude rewrites it (tone, Hinglish) but… |
| Grounding | `app/ai/guardrails.py` | …every number in Claude's text must match a number in the facts (allowing for ₹ L/Cr rounding). Otherwise the template answer is sent instead. |
| Record | `app/ai/orchestrator.py` | Question, answer, interpretation, facts, provider and grounding result are stored per message and audited. |

Follow-ups use the previous question for 30 minutes. A message that is *only* a period or a name
("and October?", "what about Pune?", "that customer") continues it; anything else starts fresh.

### What it can answer

Sales for any period · target, gap and daily run rate · month-end pace · period comparisons ·
top/bottom customers and products · one customer or product · who to follow up with (overdue
promises + customers drifting from their usual rhythm) · customers gone quiet · pipeline and
stalled deals · team ranking and who needs support · rising/declining products · a daily briefing.

Anything else gets "I can't answer that" plus examples. It never guesses.

The month-end "pace" figure is `revenue so far ÷ days elapsed × days in month`, labelled as a pace
estimate. Real forecasting models come in Phase 4.

## Switching on Claude

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-5-5
```

Claude then interprets questions (structured output into the same `Interpretation` schema) and
rewrites answers in the user's language. Requests opt into the API's server-side refusal
fallback. If Claude is unreachable, refuses, or returns something unusable, the assistant
silently uses the rule-based path, so it keeps working. The reply's "based on" line shows which
path answered.

> The Claude adapter is written against the official `anthropic` SDK (2.3.0) but has **not been
> run against the live API** in this project yet (no key was available). Test it with a key
> before relying on it.

## Connecting WhatsApp (Meta Cloud API)

1. In Meta for Developers, create an app with the WhatsApp product and a business phone number.
2. Set in `backend/.env`: `WHATSAPP_PROVIDER=meta`, `WHATSAPP_VERIFY_TOKEN` (any string you
   choose), `WHATSAPP_APP_SECRET` (App settings → Basic), `WHATSAPP_ACCESS_TOKEN` (a system-user
   token with `whatsapp_business_messaging`).
3. Expose the backend over public HTTPS (in development, e.g. a tunnel) and set the webhook in
   Meta to `https://<host>/api/v1/webhooks/whatsapp` with the same verify token; subscribe to
   **messages**.
4. In SaleSway → WhatsApp, connect the **phone number ID** (from Meta's API setup page) and add
   each user's WhatsApp number in E.164 format (`+9198…`).

Rules built in:

* Webhooks must carry a valid `X-Hub-Signature-256` (unsigned ones are accepted only when
  `ENVIRONMENT=development` and no secret is set).
* The business number decides the organisation; the sender's phone is looked up **only inside
  that organisation**. Unknown numbers get a "not registered" reply.
* Each WhatsApp message id is processed once (Meta retries deliveries).
* Replies to a user's own question are always allowed (24-hour customer-service window).
  Messages they didn't ask for, such as briefings, need opt-in: users send **START** / **STOP**,
  or an admin records consent obtained elsewhere.
* Delivery receipts (sent / delivered / read / failed) are stored on the reply.

Without Meta credentials, use **WhatsApp → Try it** to message SaleSway as any user through the
same code path; replies are captured instead of sent.

### Not built yet

* Proactive daily briefings and alerts: sending outside the 24-hour window needs **pre-approved
  message templates** and a scheduler (Celery/cron). The briefing content already exists ("brief me").
* Voice notes (speech-to-text) and images.
* Webhooks are processed inline; with an LLM at scale, move them to a queue.
