"use client";

import Link from "next/link";
import { useState } from "react";
import { FilterBar, useDashboardFilters } from "@/components/Filters";
import { SegmentedBar } from "@/components/SegmentedBar";
import { Badge, Empty, ErrorBox, SectionCard, Skeleton, StatTile, useApi, type Status } from "@/components/ui";
import type { CustomerHealth, HealthStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtRange, inr, inrCompact, number, pct } from "@/lib/format";

const STATUS: Record<HealthStatus, { status: Status; label: string }> = {
  active: { status: "good", label: "Active" },
  at_risk: { status: "warning", label: "At risk" },
  dormant: { status: "critical", label: "Dormant" },
};

const TABS: { value: HealthStatus | ""; label: string }[] = [
  { value: "", label: "Needs attention" },
  { value: "at_risk", label: "At risk" },
  { value: "dormant", label: "Dormant" },
  { value: "active", label: "Active" },
];

export default function CustomerHealthPage() {
  const { me } = useAuth();
  const { filters, update, query, today } = useDashboardFilters(me!.organization.timezone);
  const [tab, setTab] = useState<HealthStatus | "">("");

  const { product_id: _ignored, ...healthQuery } = query;
  const health = useApi<CustomerHealth>("/analytics/customer-health", healthQuery);
  const data = health.data;
  const rows = (data?.customers ?? []).filter((c) => (tab === "" ? c.status !== "active" : c.status === tab));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Customer health</h1>
        <p className="mt-1 max-w-3xl text-sm text-ink-2">
          Who is still buying, who has gone quiet, and how much revenue is at stake. Each customer is judged against their
          own usual ordering rhythm{data && <>, as of {fmtDate(data.as_of)}</>}.
        </p>
      </div>

      <FilterBar filters={filters} update={update} today={today} hide={["product_id"]} />

      {health.error ? (
        <ErrorBox message={health.error} onRetry={health.reload} />
      ) : !data ? (
        <Skeleton className="h-64" />
      ) : (
        <>
          <div className={`grid gap-4 sm:grid-cols-2 lg:grid-cols-4 ${health.loading ? "opacity-60" : ""}`}>
            <StatTile
              label="Monthly revenue at stake"
              value={inrCompact(data.summary.monthly_value_at_risk)}
              title={inr(data.summary.monthly_value_at_risk)}
              footer={<span className="text-xs text-muted">what at-risk and dormant customers used to buy per month</span>}
            />
            <StatTile label="At risk" value={number(data.summary.at_risk)} footer={<Badge status="warning" label="overdue vs their usual gap" />} />
            <StatTile label="Dormant" value={number(data.summary.dormant)} footer={<Badge status="critical" label={`no order for ${data.rules.dormant_days}+ days`} />} />
            <StatTile
              label="Buying in this period"
              value={number(data.summary.new_in_period + data.summary.returning_in_period)}
              footer={
                <span className="text-xs text-ink-2">
                  {data.summary.new_in_period} new · {data.summary.returning_in_period} returning
                  {data.summary.repeat_rate_pct !== null && <> · {pct(data.summary.repeat_rate_pct)} repeat</>}
                </span>
              }
            />
          </div>

          <SectionCard
            title="Customer base"
            subtitle={`${number(data.summary.active + data.summary.at_risk + data.summary.dormant)} customers who have ordered${
              data.summary.never_ordered ? ` · ${data.summary.never_ordered} prospects with no orders yet` : ""
            }`}
          >
            <SegmentedBar
              parts={[
                { label: `Active (${data.summary.active})`, value: data.summary.active, status: "good" },
                { label: `At risk (${data.summary.at_risk})`, value: data.summary.at_risk, status: "warning" },
                { label: `Dormant (${data.summary.dormant})`, value: data.summary.dormant, status: "critical" },
              ]}
            />
          </SectionCard>

          <SectionCard
            title="Customers"
            subtitle={`New and returning counts are for ${fmtRange(filters.start, filters.end)}. Sorted by monthly value, most at stake first.`}
            action={
              <div className="flex flex-wrap gap-1" role="tablist">
                {TABS.map((t) => (
                  <button
                    key={t.value}
                    role="tab"
                    aria-selected={tab === t.value}
                    onClick={() => setTab(t.value)}
                    className={`rounded-md px-3 py-1 text-sm ${tab === t.value ? "bg-accent-soft font-medium text-ink" : "text-ink-2 hover:bg-surface-2"}`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            }
          >
            {rows.length === 0 ? (
              <Empty title={tab === "" ? "No customers need attention" : "No customers in this group"} hint={tab === "" ? "Everyone is ordering on their usual rhythm." : undefined} />
            ) : (
              <div className="-mx-4 overflow-x-auto">
                <table className="w-full min-w-[900px]">
                  <thead>
                    <tr>
                      <th className="th">Customer</th>
                      <th className="th">Status</th>
                      <th className="th">Why</th>
                      <th className="th text-right">Last order</th>
                      <th className="th text-right">Monthly value</th>
                    </tr>
                  </thead>
                  <tbody className="tabular">
                    {rows.map((c) => (
                      <tr key={c.customer_id} className="hover:bg-surface-2">
                        <td className="td">
                          <Link href={`/customers/${c.customer_id}`} className="font-medium hover:text-accent hover:underline">
                            {c.name}
                          </Link>
                          <p className="text-xs text-muted">{[c.segment, c.owner_name].filter(Boolean).join(" · ")}</p>
                        </td>
                        <td className="td">
                          <Badge {...STATUS[c.status]} />
                        </td>
                        <td className="td max-w-xs text-ink-2">{c.reason}</td>
                        <td className="td text-right">{fmtDate(c.last_order_date)}</td>
                        <td className="td text-right font-medium">{inr(c.monthly_value)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="mt-4 text-xs text-muted">
              Rules: dormant after {data.rules.dormant_days} days without an order. At risk when the gap since the last order is more than{" "}
              {data.rules.at_risk_gap_multiplier}× the customer&apos;s usual gap (and at least {data.rules.at_risk_min_days} days). Monthly value is
              their average monthly revenue over the 6 months before their last order.
            </p>
          </SectionCard>
        </>
      )}
    </div>
  );
}
