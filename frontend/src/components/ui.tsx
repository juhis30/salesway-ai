"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { signedPct } from "@/lib/format";

// --- Data loading -----------------------------------------------------------------------

type Query = Record<string, string | number | null | undefined>;

/** Fetch `path` whenever it or `query` changes. Pass path=null to skip. */
export function useApi<T>(path: string | null, query?: Query) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [nonce, setNonce] = useState(0);
  const key = path === null ? null : `${path}?${JSON.stringify(query ?? {})}`;
  const queryRef = useRef(query);
  queryRef.current = query;

  useEffect(() => {
    if (path === null) return;
    let stale = false;
    setLoading(true);
    setError(null);
    api<T>(path, { query: queryRef.current })
      .then((result) => !stale && setData(result))
      .catch((err: Error) => !stale && setError(err.message))
      .finally(() => !stale && setLoading(false));
    return () => {
      stale = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, reload };
}

// --- States ------------------------------------------------------------------------------

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-surface-2 ${className}`} />;
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-start gap-3 rounded-lg border border-critical/40 bg-critical/5 p-4 text-sm">
      <StatusIcon status="critical" />
      <div className="flex-1">
        <p className="font-medium text-ink">Something went wrong</p>
        <p className="mt-0.5 text-ink-2">{message}</p>
      </div>
      {onRetry && (
        <button className="btn-ghost py-1" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-10 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      {hint && <p className="mt-1 max-w-sm text-sm text-ink-2">{hint}</p>}
    </div>
  );
}

// --- Status (never colour alone: icon + label) -------------------------------------------

export type Status = "good" | "warning" | "critical";

export function achievementStatus(pct: number | null): { status: Status; label: string } | null {
  if (pct === null) return null;
  if (pct >= 100) return { status: "good", label: "On target" };
  if (pct >= 85) return { status: "warning", label: "Close" };
  return { status: "critical", label: "Behind" };
}

export function StatusIcon({ status }: { status: Status }) {
  const color = { good: "text-good", warning: "text-warning", critical: "text-critical" }[status];
  return (
    <svg viewBox="0 0 16 16" className={`h-4 w-4 shrink-0 ${color}`} aria-hidden>
      <circle cx="8" cy="8" r="7" fill="currentColor" />
      {status === "good" && <path d="M4.8 8.3l2.1 2.1 4.3-4.6" stroke="#fff" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" />}
      {status === "warning" && <path d="M8 4.5v4.2M8 11.2v.2" stroke="#0b0b0b" strokeWidth="1.8" strokeLinecap="round" />}
      {status === "critical" && <path d="M5.5 5.5l5 5M10.5 5.5l-5 5" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" />}
    </svg>
  );
}

/** A status label that never relies on colour alone. */
export function Badge({ status, label }: { status: Status; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-sm text-ink-2">
      <StatusIcon status={status} />
      {label}
    </span>
  );
}

export function StatusBadge({ pct }: { pct: number | null }) {
  const s = achievementStatus(pct);
  if (!s) return <span className="text-sm text-muted">No target</span>;
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-ink-2">
      <StatusIcon status={s.status} />
      {s.label}
    </span>
  );
}

/** Achievement meter: the fill carries severity, the track is a lighter step. */
export function Meter({ pct, className = "" }: { pct: number | null; className?: string }) {
  const s = achievementStatus(pct);
  const fill = s ? { good: "bg-good", warning: "bg-warning", critical: "bg-critical" }[s.status] : "bg-muted";
  return (
    <div className={`h-2 w-full overflow-hidden rounded-full bg-surface-2 ${className}`} aria-hidden>
      <div className={`h-full rounded-full ${fill}`} style={{ width: `${Math.min(pct ?? 0, 100)}%` }} />
    </div>
  );
}

// --- Stat tile ---------------------------------------------------------------------------

export function Delta({ value, vs, upIsGood = true }: { value: number | null; vs: string; upIsGood?: boolean }) {
  if (value === null) return <span className="text-xs text-muted">No earlier data to compare with ({vs})</span>;
  const good = value === 0 ? null : value > 0 === upIsGood;
  const color = good === null ? "text-ink-2" : good ? "text-up" : "text-down";
  return (
    <span className="text-xs text-ink-2">
      <span className={`font-medium ${color}`}>
        {value > 0 ? "▲" : value < 0 ? "▼" : "■"} {signedPct(value)}
      </span>{" "}
      vs {vs}
    </span>
  );
}

export function StatTile({
  label,
  value,
  footer,
  hero = false,
  title,
}: {
  label: string;
  value: React.ReactNode;
  footer?: React.ReactNode;
  hero?: boolean;
  title?: string;
}) {
  return (
    <div className="card flex flex-col gap-1 p-4" title={title}>
      <span className="text-sm text-ink-2">{label}</span>
      <span className={hero ? "text-5xl font-semibold tracking-tight" : "text-2xl font-semibold"}>{value}</span>
      {footer && <div className="mt-1">{footer}</div>}
    </div>
  );
}

export function SectionCard({
  title,
  subtitle,
  action,
  children,
  className = "",
}: {
  title: string;
  subtitle?: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      <header className="flex flex-wrap items-start justify-between gap-2 px-4 pt-4">
        <div>
          <h2 className="text-base font-semibold">{title}</h2>
          {subtitle && <p className="mt-0.5 text-sm text-ink-2">{subtitle}</p>}
        </div>
        {action}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}
