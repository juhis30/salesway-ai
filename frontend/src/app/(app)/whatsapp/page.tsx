"use client";

import { useEffect, useRef, useState } from "react";
import { ChatText } from "@/components/ChatText";
import { Badge, Empty, ErrorBox, SectionCard, Skeleton, useApi } from "@/components/ui";
import { API_URL, api, ROLE_LABELS, type WhatsAppSettings } from "@/lib/api";
import { isAdmin, useAuth } from "@/lib/auth";

export default function WhatsAppPage() {
  const { me } = useAuth();
  if (!isAdmin(me!.user.role)) {
    return <Empty title="WhatsApp settings are for organisation admins" />;
  }
  return <WhatsAppAdmin />;
}

function WhatsAppAdmin() {
  const settings = useApi<WhatsAppSettings>("/whatsapp/settings");
  const [error, setError] = useState<string | null>(null);
  const s = settings.data;
  const webhookUrl = `${API_URL.replace(/\/api\/v1\/?$/, "")}${s?.webhook_path ?? "/api/v1/webhooks/whatsapp"}`;

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      settings.reload();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">WhatsApp</h1>
        <p className="mt-1 text-sm text-ink-2">Let your team ask SaleSway questions on WhatsApp. People are recognised by their WhatsApp number.</p>
      </div>
      {error && <ErrorBox message={error} />}

      {settings.error ? (
        <ErrorBox message={settings.error} onRetry={settings.reload} />
      ) : !s ? (
        <Skeleton className="h-64" />
      ) : (
        <>
          <SectionCard title="Connection" subtitle="Set these in the backend .env and in your Meta app's WhatsApp webhook settings.">
            <dl className="grid gap-3 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-muted">Mode</dt>
                <dd>
                  {s.provider === "meta" ? (
                    <Badge status="good" label="Live: replies are sent through the Meta Cloud API" />
                  ) : (
                    <Badge status="warning" label="Demo: replies are recorded, not sent (WHATSAPP_PROVIDER=mock)" />
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-muted">Webhook URL (must be public HTTPS in production)</dt>
                <dd className="break-all font-mono text-xs">{webhookUrl}</dd>
              </div>
              {(
                [
                  ["Verify token", s.verify_token_set, "WHATSAPP_VERIFY_TOKEN"],
                  ["App secret (signature check)", s.app_secret_set, "WHATSAPP_APP_SECRET"],
                  ["Access token (sending)", s.access_token_set, "WHATSAPP_ACCESS_TOKEN"],
                ] as const
              ).map(([label, set, env]) => (
                <div key={env}>
                  <dt className="text-muted">{label}</dt>
                  <dd>{set ? <Badge status="good" label="Set" /> : <Badge status="warning" label={`Not set (${env})`} />}</dd>
                </div>
              ))}
            </dl>
          </SectionCard>

          <SectionCard title="Business numbers" subtitle="The WhatsApp phone number ID from Meta (not the phone number itself). Messages to it are answered with this organisation's data.">
            <ul className="mb-4 divide-y divide-line">
              {s.accounts.map((a) => (
                <li key={a.id} className="flex items-center justify-between py-2 text-sm">
                  <span>
                    <span className="font-mono">{a.phone_number_id}</span>
                    {a.display_phone && <span className="ml-2 text-ink-2">{a.display_phone}</span>}
                  </span>
                  <button className="text-sm text-accent hover:underline" onClick={() => run(() => api(`/whatsapp/accounts/${a.id}`, { method: "DELETE" }))}>
                    Remove
                  </button>
                </li>
              ))}
              {s.accounts.length === 0 && <li className="py-2 text-sm text-muted">No numbers connected yet. The simulator below works without one.</li>}
            </ul>
            <AddAccount onAdd={(body) => run(() => api("/whatsapp/accounts", { method: "POST", json: body }))} />
          </SectionCard>

          <div className="grid gap-6 xl:grid-cols-[1fr_24rem]">
            <SectionCard title="People" subtitle="Only these numbers get answers. Opt-in is consent for messages they didn't ask for, like daily briefings.">
              <div className="-mx-4 overflow-x-auto">
                <table className="w-full min-w-[560px]">
                  <thead>
                    <tr>
                      <th className="th">Name</th>
                      <th className="th">WhatsApp number</th>
                      <th className="th">Briefings</th>
                    </tr>
                  </thead>
                  <tbody>
                    {s.users.map((u) => (
                      <tr key={u.id}>
                        <td className="td">
                          <p className="font-medium">{u.full_name}</p>
                          <p className="text-xs text-muted">{ROLE_LABELS[u.role]}</p>
                        </td>
                        <td className="td">
                          <PhoneEditor phone={u.phone} onSave={(phone) => run(() => api(`/users/${u.id}`, { method: "PATCH", json: { phone } }))} />
                        </td>
                        <td className="td">
                          <button
                            className="text-sm hover:underline"
                            onClick={() => run(() => api(`/users/${u.id}`, { method: "PATCH", json: { whatsapp_opt_in: !u.whatsapp_opt_in } }))}
                            title="Record consent obtained outside WhatsApp. Users can also send START or STOP."
                          >
                            {u.whatsapp_opt_in ? <Badge status="good" label="Opted in" /> : <span className="text-ink-2">Not opted in</span>}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </SectionCard>

            <Simulator users={s.users.filter((u) => u.phone)} />
          </div>
        </>
      )}
    </div>
  );
}

function AddAccount({ onAdd }: { onAdd: (body: { phone_number_id: string; display_phone: string | null }) => void }) {
  const [id, setId] = useState("");
  const [display, setDisplay] = useState("");
  return (
    <form
      className="flex flex-wrap gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        onAdd({ phone_number_id: id.trim(), display_phone: display.trim() || null });
        setId("");
        setDisplay("");
      }}
    >
      <input className="input w-56" placeholder="Phone number ID, e.g. 1234567890" required pattern="[\w-]{3,64}" value={id} onChange={(e) => setId(e.target.value)} />
      <input className="input w-48" placeholder="Display number (optional)" value={display} onChange={(e) => setDisplay(e.target.value)} />
      <button className="btn-ghost">Connect</button>
    </form>
  );
}

function PhoneEditor({ phone, onSave }: { phone: string | null; onSave: (phone: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(phone ?? "");
  if (!editing) {
    return (
      <button className="text-sm hover:underline" onClick={() => setEditing(true)}>
        {phone ? <span className="tabular">{phone}</span> : <span className="text-accent">Add number</span>}
      </button>
    );
  }
  return (
    <form
      className="flex gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(value.trim());
        setEditing(false);
      }}
    >
      <input className="input w-40 py-1" placeholder="+919876543210" pattern="^\+[1-9]\d{7,14}$" required value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
      <button className="btn-ghost py-1">Save</button>
    </form>
  );
}

interface SimMessage {
  from: "user" | "bot";
  text: string;
}

function Simulator({ users }: { users: WhatsAppSettings["users"] }) {
  const [userId, setUserId] = useState(users[0]?.id ?? "");
  const [thread, setThread] = useState<SimMessage[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [thread, busy]);
  useEffect(() => {
    setThread([]);
  }, [userId]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const message = text.trim();
    if (!message) return;
    setText("");
    setError(null);
    setBusy(true);
    setThread((t) => [...t, { from: "user", text: message }]);
    try {
      const res = await api<{ reply: string }>("/whatsapp/simulate", { method: "POST", json: { user_id: userId, text: message } });
      setThread((t) => [...t, { from: "bot", text: res.reply }]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const user = users.find((u) => u.id === userId);
  return (
    <SectionCard title="Try it" subtitle="Message SaleSway as one of your people. Their permissions apply. Nothing is sent to a phone.">
      {users.length === 0 ? (
        <Empty title="Add a WhatsApp number to someone first" />
      ) : (
        <>
          <select className="input mb-3" value={userId} onChange={(e) => setUserId(e.target.value)} aria-label="Send as">
            {users.map((u) => (
              <option key={u.id} value={u.id}>
                {u.full_name} ({ROLE_LABELS[u.role]})
              </option>
            ))}
          </select>
          <div className="flex h-96 flex-col overflow-hidden rounded-2xl border border-line bg-surface-2">
            <div className="border-b border-line bg-surface px-3 py-2 text-xs text-ink-2">
              <span className="font-medium text-ink">SaleSway</span> · chatting as {user?.full_name} ({user?.phone})
            </div>
            <div className="flex-1 space-y-2 overflow-y-auto p-3">
              {thread.length === 0 && <p className="pt-10 text-center text-xs text-muted">Try “is mahine kitna becha?” or “who should I follow up with?”</p>}
              {thread.map((m, i) => (
                <div key={i} className={`flex ${m.from === "user" ? "justify-end" : "justify-start"}`}>
                  <div className={`max-w-[85%] rounded-xl px-3 py-2 shadow-sm ${m.from === "user" ? "bg-accent-soft" : "bg-surface"}`}>
                    <ChatText text={m.text} />
                  </div>
                </div>
              ))}
              {busy && <p className="text-xs text-muted">typing…</p>}
              <div ref={bottom} />
            </div>
            <form onSubmit={send} className="flex gap-2 border-t border-line bg-surface p-2">
              <input className="input flex-1 py-1.5" placeholder="Message" value={text} maxLength={1000} onChange={(e) => setText(e.target.value)} aria-label="Message" />
              <button className="btn-primary py-1.5" disabled={busy || !text.trim()}>Send</button>
            </form>
          </div>
          {error && <p className="mt-2 text-sm text-critical">{error}</p>}
        </>
      )}
    </SectionCard>
  );
}
