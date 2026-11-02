"use client";

import { useEffect, useRef, useState } from "react";
import { ChatText } from "@/components/ChatText";
import { ErrorBox, useApi } from "@/components/ui";
import { api, type ChatMessage, type ConversationSummary } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtRange } from "@/lib/format";

const STARTERS = [
  "How much have I sold this month?",
  "Am I on track for my target?",
  "Who should I follow up with today?",
  "Top 5 customers last month",
  "Which products are declining?",
  "Which deals are stalled?",
  "is mahine kitna becha?",
  "Brief me",
];

const PROVIDER_LABEL: Record<string, string> = {
  "rule-based": "rule-based assistant",
  "rule-based (fallback)": "rule-based assistant (model unavailable)",
  anthropic: "Claude",
};

function BasedOn({ message }: { message: ChatMessage }) {
  const b = message.based_on;
  if (!b) return null;
  const parts = [
    b.scope,
    b.period && `${fmtRange(b.period[0], b.period[1])}${b.comparison ? ` vs ${fmtRange(b.comparison[0], b.comparison[1])}` : ""}`,
    b.data_as_of && `data up to ${fmtDate(b.data_as_of)}`,
    b.provider && (PROVIDER_LABEL[b.provider] ?? b.provider),
  ].filter(Boolean);
  return (
    <p className="mt-2 text-xs text-muted">
      Based on: {parts.join(" · ")}
      {!b.grounded && " · the model's wording was replaced by the verified answer"}
    </p>
  );
}

export default function AssistantPage() {
  const { me } = useAuth();
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const conversations = useApi<ConversationSummary[]>("/assistant/conversations");
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sending]);

  async function open(id: string) {
    setError(null);
    setShowHistory(false);
    try {
      setMessages(await api<ChatMessage[]>(`/assistant/conversations/${id}`));
      setConversationId(id);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  function startNew() {
    setConversationId(null);
    setMessages([]);
    setError(null);
    setShowHistory(false);
  }

  async function send(text: string) {
    const question = text.trim();
    if (!question || sending) return;
    setInput("");
    setError(null);
    setSending(true);
    const pending: ChatMessage = {
      id: `pending-${Date.now()}`, role: "user", content: question, intent: null,
      created_at: new Date().toISOString(), based_on: null, suggestions: [],
    };
    setMessages((m) => [...m, pending]);
    try {
      const res = await api<{ conversation_id: string; message: ChatMessage }>("/assistant/messages", {
        method: "POST",
        json: { text: question, conversation_id: conversationId },
      });
      setConversationId(res.conversation_id);
      setMessages((m) => [...m, res.message]);
      if (!conversationId) conversations.reload();
    } catch (err) {
      setError((err as Error).message);
      setMessages((m) => m.filter((x) => x.id !== pending.id));
      setInput(question);
    } finally {
      setSending(false);
    }
  }

  const last = [...messages].reverse().find((m) => m.role === "assistant");
  const firstName = me!.user.full_name.split(" ")[0];

  return (
    <div className="flex h-[calc(100vh-7.5rem)] gap-6">
      <aside className={`${showHistory ? "fixed inset-0 z-30 flex bg-page p-4" : "hidden"} w-full flex-col md:static md:flex md:w-64 md:shrink-0 md:p-0`}>
        <div className="mb-3 flex items-center justify-between gap-2">
          <button className="btn-primary flex-1" onClick={startNew}>New chat</button>
          <button className="btn-ghost md:hidden" onClick={() => setShowHistory(false)} aria-label="Close history">✕</button>
        </div>
        <p className="mb-1 px-1 text-xs font-medium text-muted">Recent</p>
        <ul className="-mx-1 flex-1 space-y-0.5 overflow-y-auto">
          {(conversations.data ?? []).map((c) => (
            <li key={c.id}>
              <button
                onClick={() => open(c.id)}
                className={`w-full truncate rounded-lg px-2 py-1.5 text-left text-sm ${c.id === conversationId ? "bg-accent-soft font-medium" : "text-ink-2 hover:bg-surface-2"}`}
                title={c.title ?? ""}
              >
                {c.channel === "whatsapp" && <span className="mr-1 text-xs text-muted">WhatsApp ·</span>}
                {c.title ?? "New chat"}
              </button>
            </li>
          ))}
          {conversations.data?.length === 0 && <li className="px-2 text-sm text-muted">No chats yet</li>}
        </ul>
      </aside>

      <section className="card flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-line px-4 py-3">
          <div>
            <h1 className="font-semibold">Ask SaleSway</h1>
            <p className="text-xs text-muted">Answers come from your data, within what your role can see. Numbers are never guessed.</p>
          </div>
          <button className="btn-ghost py-1 md:hidden" onClick={() => setShowHistory(true)}>History</button>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto p-4" aria-live="polite">
          {messages.length === 0 && (
            <div className="mx-auto max-w-xl py-8 text-center">
              <p className="text-lg font-semibold">Hi {firstName}, what would you like to know?</p>
              <p className="mt-1 text-sm text-ink-2">Ask in English or Hinglish. Follow-ups like “and last month?” work too.</p>
              <div className="mt-6 flex flex-wrap justify-center gap-2">
                {STARTERS.map((s) => (
                  <button key={s} onClick={() => send(s)} className="rounded-full border border-line px-3 py-1.5 text-sm hover:bg-surface-2">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="flex justify-end">
                <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-accent-soft px-4 py-2 text-sm">{m.content}</div>
              </div>
            ) : (
              <div key={m.id} className="flex justify-start">
                <div className="max-w-[90%] rounded-2xl rounded-bl-md border border-line bg-surface-2/60 px-4 py-3">
                  <ChatText text={m.content} />
                  <BasedOn message={m} />
                </div>
              </div>
            ),
          )}
          {sending && (
            <div className="flex justify-start">
              <div className="rounded-2xl border border-line bg-surface-2/60 px-4 py-3 text-sm text-muted">Looking at your numbers…</div>
            </div>
          )}
          <div ref={bottom} />
        </div>

        {error && (
          <div className="px-4 pb-2">
            <ErrorBox message={error} />
          </div>
        )}
        {last && last.suggestions.length > 0 && !sending && (
          <div className="flex flex-wrap gap-2 px-4 pb-2">
            {last.suggestions.map((s) => (
              <button key={s} onClick={() => send(s)} className="rounded-full border border-line px-3 py-1 text-xs text-ink-2 hover:bg-surface-2">
                {s}
              </button>
            ))}
          </div>
        )}
        <form
          className="flex items-end gap-2 border-t border-line p-3"
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
        >
          <textarea
            className="input max-h-32 min-h-[2.5rem] flex-1 resize-none"
            rows={1}
            maxLength={1000}
            placeholder="Ask about sales, targets, customers, products or deals…"
            value={input}
            aria-label="Your question"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
          />
          <button className="btn-primary" disabled={sending || !input.trim()}>Send</button>
        </form>
      </section>
    </div>
  );
}
