"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthCard } from "@/components/AuthCard";
import { useAuth } from "@/lib/auth";

const DEMO_USERS = [
  ["vikram@shakti-demo.in", "Sales head"],
  ["sanjay@shakti-demo.in", "Regional manager"],
  ["rahul@shakti-demo.in", "Sales manager"],
  ["amit@shakti-demo.in", "Sales executive"],
  ["ananya@shakti-demo.in", "Admin"],
];

export default function LoginPage() {
  const { me, login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (me) router.replace("/dashboard");
  }, [me, router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard title="Sign in" subtitle="Your sales numbers, in one place.">
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="label" htmlFor="email">Email</label>
          <input id="email" type="email" className="input" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="password">Password</label>
          <input id="password" type="password" className="input" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {error && <p role="alert" className="text-sm text-critical">{error}</p>}
        <button className="btn-primary w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>

      <p className="mt-4 text-center text-sm text-ink-2">
        New company? <Link href="/register" className="font-medium text-accent hover:underline">Create an account</Link>
      </p>

      <div className="mt-6 border-t border-line pt-4">
        <p className="text-xs font-medium text-ink-2">Demo accounts (password Demo@12345)</p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {DEMO_USERS.map(([demoEmail, label]) => (
            <button
              key={demoEmail}
              type="button"
              className="rounded-md border border-line px-2 py-1 text-xs text-ink-2 hover:bg-surface-2"
              onClick={() => {
                setEmail(demoEmail);
                setPassword("Demo@12345");
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
    </AuthCard>
  );
}
