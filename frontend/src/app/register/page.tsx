"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthCard } from "@/components/AuthCard";
import { useAuth } from "@/lib/auth";

export default function RegisterPage() {
  const { me, register } = useAuth();
  const router = useRouter();
  const [form, setForm] = useState({ organization_name: "", industry: "", full_name: "", email: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // A brand-new organisation has no data yet, so start on the import screen.
    if (me) router.replace("/imports");
  }, [me, router]);

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await register({ ...form, industry: form.industry || undefined });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard title="Create your company account" subtitle="You'll be the admin. Add your team and upload data next.">
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="label" htmlFor="org">Company name</label>
          <input id="org" className="input" required minLength={2} value={form.organization_name} onChange={set("organization_name")} />
        </div>
        <div>
          <label className="label" htmlFor="industry">Industry (optional)</label>
          <input id="industry" className="input" placeholder="e.g. FMCG distribution" value={form.industry} onChange={set("industry")} />
        </div>
        <div>
          <label className="label" htmlFor="name">Your name</label>
          <input id="name" className="input" required value={form.full_name} onChange={set("full_name")} />
        </div>
        <div>
          <label className="label" htmlFor="email">Work email</label>
          <input id="email" type="email" className="input" autoComplete="email" required value={form.email} onChange={set("email")} />
        </div>
        <div>
          <label className="label" htmlFor="password">Password</label>
          <input id="password" type="password" className="input" autoComplete="new-password" required minLength={8} maxLength={72} value={form.password} onChange={set("password")} />
          <p className="mt-1 text-xs text-muted">At least 8 characters.</p>
        </div>
        {error && <p role="alert" className="text-sm text-critical">{error}</p>}
        <button className="btn-primary w-full" disabled={busy}>{busy ? "Creating…" : "Create account"}</button>
      </form>
      <p className="mt-4 text-center text-sm text-ink-2">
        Already have an account? <Link href="/login" className="font-medium text-accent hover:underline">Sign in</Link>
      </p>
    </AuthCard>
  );
}
