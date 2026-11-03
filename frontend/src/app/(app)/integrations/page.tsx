"use client";

import { useEffect, useState } from "react";
import { Badge, Empty, ErrorBox, SectionCard, Skeleton, useApi, type Status } from "@/components/ui";
import { api, type Provider, type SyncJobInfo } from "@/lib/api";
import { isAdmin, useAuth } from "@/lib/auth";

const ENTITY_LABELS: Record<string, string> = {
  customers: "Customers",
  products: "Products",
  orders: "Orders",
  opportunities: "Deals",
  activities: "Activities",
};

const CONNECTION: Record<string, { status: Status; label: string }> = {
  connected: { status: "good", label: "Connected" },
  needs_reconnect: { status: "critical", label: "Needs reconnecting" },
  error: { status: "warning", label: "Last sync failed" },
};

const JOB: Record<SyncJobInfo["status"], { status: Status; label: string }> = {
  succeeded: { status: "good", label: "Synced" },
  partial: { status: "warning", label: "Synced with some rows skipped" },
  failed: { status: "critical", label: "Failed" },
  running: { status: "warning", label: "Running" },
};

function when(iso: string | null): string {
  if (!iso) return "never";
  return new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function summary(counts: Record<string, number>): string {
  const created = (counts.created ?? 0) + (counts.orders_created ?? 0);
  const updated = (counts.updated ?? 0) + (counts.orders_status_updated ?? 0);
  const parts = [`${counts.fetched ?? 0} received`];
  if (created) parts.push(`${created} new`);
  if (updated) parts.push(`${updated} updated`);
  if (counts.errors) parts.push(`${counts.errors} skipped`);
  if (counts.fetched && !created && !updated && !counts.errors) parts.push("all already up to date");
  return parts.join(", ");
}

export default function IntegrationsPage() {
  const { me } = useAuth();
  if (!isAdmin(me!.user.role)) return <Empty title="Connections are managed by organisation admins" />;
  return <Integrations />;
}

function Integrations() {
  const providers = useApi<Provider[]>("/integrations");
  const [notice, setNotice] = useState<{ kind: "good" | "critical"; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // Coming back from a CRM's sign-in page.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("connected")) setNotice({ kind: "good", text: "Connected. Select “Sync now” to bring your data in." });
    if (params.get("error")) setNotice({ kind: "critical", text: params.get("error")! });
    if (params.toString()) window.history.replaceState(null, "", "/integrations");
  }, []);

  async function act(key: string, action: () => Promise<void>) {
    setBusy(key);
    setNotice(null);
    try {
      await action();
    } catch (err) {
      setNotice({ kind: "critical", text: (err as Error).message });
    } finally {
      setBusy(null);
      providers.reload();
    }
  }

  const connect = (p: Provider) =>
    act(p.provider, async () => {
      const res = await api<{ authorize_url?: string; connected?: boolean }>(`/integrations/${p.provider}/connect`, { method: "POST" });
      if (res.authorize_url) window.location.href = res.authorize_url;
      else setNotice({ kind: "good", text: `${p.label} connected. Select “Sync now” to bring its data in.` });
    });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Connections</h1>
        <p className="mt-1 max-w-3xl text-sm text-ink-2">
          Bring data in from your CRM automatically. Connections are <strong>read-only</strong>: SaleSway copies records in and never
          changes anything in your CRM. Salespeople are matched by email, so their CRM email must match their SaleSway login.
        </p>
      </div>

      {notice && (
        <div role="status" className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${notice.kind === "good" ? "border-good/40 bg-good/5" : "border-critical/40 bg-critical/5"}`}>
          <Badge status={notice.kind} label={notice.text} />
        </div>
      )}

      {providers.error ? (
        <ErrorBox message={providers.error} onRetry={providers.reload} />
      ) : !providers.data ? (
        <Skeleton className="h-96" />
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          {providers.data.map((p) => (
            <SectionCard
              key={p.provider}
              title={p.label}
              subtitle={p.description}
              action={p.integration && <Badge {...CONNECTION[p.integration.status]} />}
            >
              <div className="flex flex-wrap gap-1.5">
                {p.entities.map((e) => (
                  <span key={e} className="rounded-full bg-surface-2 px-2.5 py-0.5 text-xs text-ink-2">{ENTITY_LABELS[e] ?? e}</span>
                ))}
              </div>
              {p.not_synced && <p className="mt-2 text-xs text-muted">{p.not_synced}</p>}

              {p.integration ? (
                <>
                  <dl className="mt-4 grid grid-cols-2 gap-2 text-sm">
                    <div><dt className="text-muted">Account</dt><dd>{p.integration.account_label ?? "—"}</dd></div>
                    <div><dt className="text-muted">Last sync</dt><dd>{when(p.integration.last_sync_at)}</dd></div>
                  </dl>
                  {p.integration.last_error && <p className="mt-2 text-sm text-critical">{p.integration.last_error}</p>}
                  <div className="mt-4 flex flex-wrap gap-2">
                    <button
                      className="btn-primary"
                      disabled={busy !== null}
                      onClick={() => act(`sync-${p.provider}`, async () => {
                        const job = await api<SyncJobInfo>(`/integrations/${p.integration!.id}/sync`, { method: "POST" });
                        setNotice({ kind: job.status === "failed" ? "critical" : "good",
                                    text: job.status === "failed" ? job.message ?? "Sync failed" : `${p.label}: ${job.records} records received.` });
                      })}
                    >
                      {busy === `sync-${p.provider}` ? "Syncing…" : "Sync now"}
                    </button>
                    {p.integration.status === "needs_reconnect" && (
                      <button className="btn-ghost" disabled={busy !== null} onClick={() => connect(p)}>Reconnect</button>
                    )}
                    <button
                      className="btn-ghost"
                      disabled={busy !== null}
                      onClick={() => {
                        if (window.confirm(`Disconnect ${p.label}? Data already synced stays in SaleSway.`)) {
                          act(`del-${p.provider}`, () => api(`/integrations/${p.integration!.id}`, { method: "DELETE" }));
                        }
                      }}
                    >
                      Disconnect
                    </button>
                  </div>
                  <History integrationId={p.integration.id} refreshKey={p.integration.last_sync_at ?? ""} />
                </>
              ) : (
                <div className="mt-4">
                  {p.configured ? (
                    <button className="btn-primary" disabled={busy !== null} onClick={() => connect(p)}>
                      {busy === p.provider ? "Opening…" : `Connect ${p.label}`}
                    </button>
                  ) : (
                    <p className="text-sm text-ink-2">
                      Not set up on this server yet. An administrator needs to register a SaleSway app with {p.label} and set{" "}
                      {p.missing_settings.map((m, i) => (
                        <span key={m}><code className="rounded bg-surface-2 px-1 text-xs">{m}</code>{i < p.missing_settings.length - 1 ? ", " : ""}</span>
                      ))}
                      . See docs/integrations.md.
                    </p>
                  )}
                </div>
              )}
            </SectionCard>
          ))}
        </div>
      )}
    </div>
  );
}

function History({ integrationId, refreshKey }: { integrationId: string; refreshKey: string }) {
  const jobs = useApi<SyncJobInfo[]>(`/integrations/${integrationId}/jobs`, { limit: 5, k: refreshKey });
  const [open, setOpen] = useState<string | null>(null);
  if (!jobs.data || jobs.data.length === 0) return null;
  return (
    <div className="mt-5 border-t border-line pt-3">
      <p className="mb-2 text-xs font-medium text-muted">Recent syncs</p>
      <ul className="space-y-2">
        {jobs.data.map((j) => (
          <li key={j.id} className="text-sm">
            <button className="flex w-full flex-wrap items-center justify-between gap-2 text-left" onClick={() => setOpen(open === j.id ? null : j.id)} aria-expanded={open === j.id}>
              <span className="text-ink-2">{when(j.started_at)} · {j.trigger}</span>
              <Badge {...JOB[j.status]} />
            </button>
            {open === j.id && (
              <div className="mt-2 rounded-lg bg-surface-2 p-3 text-xs">
                {j.message && <p className="mb-2 text-critical">{j.message}</p>}
                <ul className="space-y-0.5">
                  {Object.entries(j.counts).map(([entity, c]) => (
                    <li key={entity}><span className="font-medium">{ENTITY_LABELS[entity] ?? entity}:</span> {summary(c)}</li>
                  ))}
                </ul>
                {j.errors.length > 0 && (
                  <>
                    <p className="mt-2 font-medium">Skipped rows</p>
                    <ul className="mt-1 space-y-0.5 text-ink-2">
                      {j.errors.slice(0, 10).map((e, i) => (
                        <li key={i}>{ENTITY_LABELS[e.entity] ?? e.entity}: {e.field && <code>{e.field}</code>} {e.message}</li>
                      ))}
                      {j.errors.length > 10 && <li>…and {j.errors.length - 10} more</li>}
                    </ul>
                  </>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
