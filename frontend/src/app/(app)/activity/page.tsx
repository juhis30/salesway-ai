"use client";

import Link from "next/link";
import { useState } from "react";
import { FilterBar, useDashboardFilters } from "@/components/Filters";
import { Badge, Empty, ErrorBox, SectionCard, Skeleton, StatTile, useApi } from "@/components/ui";
import { ACTIVITY_LABELS, api, type FollowUp, type TeamActivity } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtRange, inr, inrCompact, number, pct } from "@/lib/format";

export default function TeamActivityPage() {
  const { me } = useAuth();
  const { filters, update, query, today } = useDashboardFilters(me!.organization.timezone, "last_3_months");

  const { product_id: _ignored, ...activityQuery } = query;
  const activity = useApi<TeamActivity>("/analytics/team-activity", activityQuery);
  const data = activity.data;
  const maxVisits = Math.max(...(data?.people ?? []).map((p) => p.visits), 1);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Team activity</h1>
        <p className="mt-1 text-sm text-ink-2">Visits, calls and follow-ups, and whether field work is turning into orders.</p>
      </div>

      <FilterBar filters={filters} update={update} today={today} hide={["product_id"]} />

      {activity.error ? (
        <ErrorBox message={activity.error} onRetry={activity.reload} />
      ) : !data ? (
        <Skeleton className="h-96" />
      ) : (
        <>
          <div className={`grid gap-4 sm:grid-cols-2 lg:grid-cols-4 ${activity.loading ? "opacity-60" : ""}`}>
            <StatTile label="Visits" value={number(data.totals.visits)} footer={<span className="text-xs text-muted">{fmtRange(data.period.start, data.period.end)}</span>} />
            <StatTile label="Calls & messages" value={number(data.totals.calls + data.totals.whatsapp)} footer={<span className="text-xs text-muted">{number(data.totals.calls)} calls · {number(data.totals.whatsapp)} WhatsApp</span>} />
            <StatTile label="Meetings" value={number(data.totals.meetings)} />
            <StatTile
              label="Overdue follow-ups"
              value={number(data.totals.follow_ups_overdue)}
              footer={data.totals.follow_ups_overdue > 0 ? <Badge status="critical" label="promised but not done" /> : <Badge status="good" label="all on time" />}
            />
          </div>

          <SectionCard
            title="By salesperson"
            subtitle={`Visit → order = share of visits followed by an order from that customer within ${data.rules.visit_to_order_days} days.`}
          >
            {data.people.length === 0 ? (
              <Empty title="No activity recorded in this period" hint="Import an activities file to see field work here." />
            ) : (
              <div className="-mx-4 overflow-x-auto">
                <table className="w-full min-w-[980px]">
                  <thead>
                    <tr>
                      <th className="th">Salesperson</th>
                      <th className="th w-52">Visits</th>
                      <th className="th text-right">Calls</th>
                      <th className="th text-right">Meetings</th>
                      <th className="th text-right">WhatsApp</th>
                      <th className="th text-right">Customers contacted</th>
                      <th className="th text-right">Visit → order</th>
                      <th className="th text-right">Revenue per visit</th>
                      <th className="th">Follow-ups</th>
                    </tr>
                  </thead>
                  <tbody className="tabular">
                    {data.people.map((p) => (
                      <tr key={p.salesperson_id} className="hover:bg-surface-2">
                        <td className="td">
                          <p className="font-medium">{p.name}</p>
                          <p className="text-xs text-muted">
                            {number(p.orders)} orders · {inrCompact(p.revenue)}
                          </p>
                        </td>
                        <td className="td">
                          <div className="flex items-center gap-2" title={`${p.visits} visits`}>
                            <span className="w-10 text-right">{number(p.visits)}</span>
                            <div className="h-2 flex-1 rounded-full bg-surface-2">
                              <div className="h-full rounded-full" style={{ width: `${(p.visits / maxVisits) * 100}%`, background: "var(--series-1)" }} />
                            </div>
                          </div>
                        </td>
                        <td className="td text-right">{number(p.calls)}</td>
                        <td className="td text-right">{number(p.meetings)}</td>
                        <td className="td text-right">{number(p.whatsapp)}</td>
                        <td className="td text-right">{number(p.customers_contacted)}</td>
                        <td className="td text-right">{pct(p.visit_to_order_pct, 0)}</td>
                        <td className="td text-right">{inr(p.revenue_per_visit)}</td>
                        <td className="td">
                          {p.follow_ups_overdue > 0 ? (
                            <Badge status={p.follow_ups_overdue > 3 ? "critical" : "warning"} label={`${p.follow_ups_overdue} overdue`} />
                          ) : (
                            <span className="text-sm text-ink-2">None overdue</span>
                          )}
                          <p className="text-xs text-muted">{p.follow_ups_upcoming} coming up</p>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </SectionCard>
        </>
      )}

      <FollowUps query={activityQuery} onChange={activity.reload} />
    </div>
  );
}

function FollowUps({ query, onChange }: { query: Record<string, string>; onChange: () => void }) {
  const [when, setWhen] = useState<"overdue" | "upcoming">("overdue");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const items = useApi<FollowUp[]>("/activities/follow-ups", { ...query, when });

  async function markDone(id: string) {
    setBusy(id);
    setError(null);
    try {
      await api(`/activities/${id}`, { method: "PATCH", json: { status: "done" } });
      items.reload();
      onChange();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <SectionCard
      title="Follow-ups"
      subtitle="Visits and calls people have planned."
      action={
        <div className="flex gap-1" role="tablist">
          {(["overdue", "upcoming"] as const).map((w) => (
            <button
              key={w}
              role="tab"
              aria-selected={when === w}
              onClick={() => setWhen(w)}
              className={`rounded-md px-3 py-1 text-sm capitalize ${when === w ? "bg-accent-soft font-medium text-ink" : "text-ink-2 hover:bg-surface-2"}`}
            >
              {w}
            </button>
          ))}
        </div>
      }
    >
      {error && (
        <div className="mb-3">
          <ErrorBox message={error} />
        </div>
      )}
      {items.error ? (
        <ErrorBox message={items.error} onRetry={items.reload} />
      ) : !items.data ? (
        <Skeleton className="h-32" />
      ) : items.data.length === 0 ? (
        <Empty title={when === "overdue" ? "Nothing overdue" : "Nothing planned"} />
      ) : (
        <ul className="-mx-4 -mb-4">
          {items.data.map((f) => (
            <li key={f.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line px-4 py-3 text-sm">
              <span className="w-28 shrink-0">
                <span className="tabular">{fmtDate(f.due_date)}</span>
                {f.days_overdue > 0 && <span className="block text-xs text-critical">{f.days_overdue} days late</span>}
              </span>
              <span className="min-w-0 flex-1">
                <span className="font-medium">{ACTIVITY_LABELS[f.activity_type]}</span>
                {f.customer_name && (
                  <>
                    {" "}
                    with{" "}
                    <Link href={`/customers/${f.customer_id}`} className="font-medium hover:text-accent hover:underline">
                      {f.customer_name}
                    </Link>
                  </>
                )}
                <span className="block text-xs text-muted">
                  {[f.salesperson_name, f.opportunity_name, f.notes].filter(Boolean).join(" · ")}
                </span>
              </span>
              <button className="btn-ghost py-1" disabled={busy === f.id} onClick={() => markDone(f.id)}>
                {busy === f.id ? "Saving…" : "Mark done"}
              </button>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}
