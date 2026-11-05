"use client";

import { useState } from "react";
import { SectionCard } from "@/components/ui";
import { api, PASSWORD_HINT, ROLE_LABELS, setTokens } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function AccountPage() {
  const { me } = useAuth();
  const demo = me!.organization.plan === "demo";
  const empty = { current_password: "", new_password: "", confirm: "" };
  const [form, setForm] = useState(empty);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setDone(false);
    if (form.new_password !== form.confirm) {
      setError("The new passwords don't match.");
      return;
    }
    setBusy(true);
    try {
      // Every other session is signed out; this one continues with the new tokens.
      setTokens(await api<{ access_token: string; refresh_token: string }>("/auth/change-password", {
        method: "POST",
        json: { current_password: form.current_password, new_password: form.new_password },
      }));
      setForm(empty);
      setDone(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Your account</h1>
        <p className="mt-1 text-sm text-ink-2">
          {me!.user.full_name} · {me!.user.email} · {ROLE_LABELS[me!.user.role]}
        </p>
      </div>

      <SectionCard title="Change password" subtitle={demo ? undefined : "You'll stay signed in here; every other device is signed out."}>
        {demo ? (
          <p className="text-sm text-ink-2">
            Passwords can't be changed in the shared demo, because everyone signs in with the same accounts.
            Register your own organisation to try this.
          </p>
        ) : (
          <form onSubmit={submit} className="max-w-sm space-y-4">
            <div>
              <label className="label" htmlFor="current">Current password</label>
              <input id="current" type="password" className="input" autoComplete="current-password" required maxLength={72}
                value={form.current_password} onChange={set("current_password")} />
            </div>
            <div>
              <label className="label" htmlFor="new">New password</label>
              <input id="new" type="password" className="input" autoComplete="new-password" required minLength={10} maxLength={72}
                aria-describedby="new-hint" value={form.new_password} onChange={set("new_password")} />
              <p id="new-hint" className="mt-1 text-xs text-muted">{PASSWORD_HINT}</p>
            </div>
            <div>
              <label className="label" htmlFor="confirm">Repeat new password</label>
              <input id="confirm" type="password" className="input" autoComplete="new-password" required maxLength={72}
                value={form.confirm} onChange={set("confirm")} />
            </div>
            <button className="btn-primary" disabled={busy}>{busy ? "Saving…" : "Change password"}</button>
            {error && <p role="alert" className="text-sm text-critical">{error}</p>}
            {done && <p role="status" className="text-sm text-good">Password changed. Other devices have been signed out.</p>}
          </form>
        )}
      </SectionCard>
    </div>
  );
}
