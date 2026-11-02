"use client";

import Link from "next/link";
import { useState } from "react";
import { FilterBar, useDashboardFilters } from "@/components/Filters";
import { Badge, Empty, ErrorBox, SectionCard, Skeleton, StatTile, useApi } from "@/components/ui";
import { STAGE_LABELS, type Pipeline } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtRange, inr, inrCompact, number, pct } from "@/lib/format";

type DealFilter = "all" | "stalled" | "overdue";

export default function PipelinePage() {
  const { me } = useAuth();
  const { filters, update, query, today } = useDashboardFilters(me!.organization.timezone, "last_3_months");
  const [dealFilter, setDealFilter] = useState<DealFilter>("all");

  const { product_id: _ignored, ...pipelineQuery } = query;
  const pipeline = useApi<Pipeline>("/analytics/pipeline", pipelineQuery);
  const data = pipeline.data;

  const deals = (data?.deals ?? []).filter((d) => dealFilter === "all" || d[dealFilter]);
  const maxStage = Math.max(...(data?.by_stage ?? []).map((s) => Number(s.value)), 1);
  const closedCount = data ? data.won_count + data.lost_count : 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Sales pipeline</h1>
        <p className="mt-1 text-sm text-ink-2">
          Open deals as of today{data && <> ({fmtDate(data.as_of)})</>}. Won, lost and win rate are for deals closed in the selected period.
        </p>
      </div>

      <FilterBar filters={filters} update={update} today={today} hide={["product_id"]} periodLabel="Closed in" />

      {pipeline.error ? (
        <ErrorBox message={pipeline.error} onRetry={pipeline.reload} />
      ) : !data ? (
        <Skeleton className="h-96" />
      ) : (
        <>
          <div className={`grid gap-4 sm:grid-cols-2 lg:grid-cols-4 ${pipeline.loading ? "opacity-60" : ""}`}>
            <StatTile
              label="Open pipeline"
              value={inrCompact(data.open_value)}
              title={inr(data.open_value)}
              footer={<span className="text-xs text-ink-2">{number(data.open_count)} open deals</span>}
            />
            <StatTile
              label="Weighted pipeline"
              value={inrCompact(data.weighted_value)}
              title={inr(data.weighted_value)}
              footer={<span className="text-xs text-muted">each deal × its stage weight (below)</span>}
            />
            <StatTile
              label="Win rate"
              value={pct(data.win_rate_pct, 0)}
              footer={
                <span className="text-xs text-ink-2">
                  {closedCount ? `${data.won_count} won · ${data.lost_count} lost in ${fmtRange(data.period.start, data.period.end)}` : "No deals closed in this period"}
                </span>
              }
            />
            <StatTile
              label="Average won deal"
              value={inrCompact(data.average_won_deal)}
              title={inr(data.average_won_deal)}
              footer={data.average_cycle_days !== null && <span className="text-xs text-ink-2">{data.average_cycle_days} days from open to won</span>}
            />
          </div>

          <SectionCard title="Open deals by stage" subtitle="Stage weights are fixed conventions for estimating, not predictions.">
            <ul className="space-y-4">
              {data.by_stage.map((s) => (
                <li key={s.stage} title={`${STAGE_LABELS[s.stage]}: ${s.count} deals, ${inr(s.value)}`}>
                  <div className="flex items-baseline justify-between gap-3 text-sm">
                    <span>
                      <span className="font-medium">{STAGE_LABELS[s.stage]}</span>
                      <span className="ml-2 text-xs text-muted">
                        {s.count} deal{s.count === 1 ? "" : "s"} · weight {s.weight_pct}%
                      </span>
                    </span>
                    <span className="tabular">
                      <span className="font-medium">{inrCompact(s.value)}</span>
                      <span className="ml-2 text-xs text-muted">weighted {inrCompact(s.weighted_value)}</span>
                    </span>
                  </div>
                  <div className="mt-1.5 h-2 rounded-full bg-surface-2">
                    <div className="h-full rounded-full" style={{ width: `${(Number(s.value) / maxStage) * 100}%`, background: "var(--series-1)" }} />
                  </div>
                </li>
              ))}
            </ul>
          </SectionCard>

          <SectionCard
            title="Open deals"
            subtitle={`Stalled = no activity for more than ${data.rules.stalled_days} days. Overdue = expected close date has passed.`}
            action={
              <div className="flex flex-wrap gap-1" role="tablist">
                {(
                  [
                    ["all", `All (${data.open_count})`],
                    ["stalled", `Stalled (${data.stalled_count})`],
                    ["overdue", `Overdue (${data.overdue_count})`],
                  ] as [DealFilter, string][]
                ).map(([value, label]) => (
                  <button
                    key={value}
                    role="tab"
                    aria-selected={dealFilter === value}
                    onClick={() => setDealFilter(value)}
                    className={`rounded-md px-3 py-1 text-sm ${dealFilter === value ? "bg-accent-soft font-medium text-ink" : "text-ink-2 hover:bg-surface-2"}`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            }
          >
            {deals.length === 0 ? (
              <Empty title={dealFilter === "all" ? "No open deals" : `No ${dealFilter} deals`} />
            ) : (
              <div className="-mx-4 overflow-x-auto">
                <table className="w-full min-w-[960px]">
                  <thead>
                    <tr>
                      <th className="th">Deal</th>
                      <th className="th">Stage</th>
                      <th className="th text-right">Value</th>
                      <th className="th">Expected close</th>
                      <th className="th">Last activity</th>
                      <th className="th text-right">Open for</th>
                    </tr>
                  </thead>
                  <tbody className="tabular">
                    {deals.map((d) => (
                      <tr key={d.opportunity_id} className="hover:bg-surface-2">
                        <td className="td">
                          <p className="font-medium">{d.name}</p>
                          <p className="text-xs text-muted">
                            {d.code} ·{" "}
                            <Link href={`/customers/${d.customer_id}`} className="hover:text-accent hover:underline">
                              {d.customer_name}
                            </Link>
                            {d.owner_name && ` · ${d.owner_name}`}
                          </p>
                        </td>
                        <td className="td">{STAGE_LABELS[d.stage]}</td>
                        <td className="td text-right font-medium">{inr(d.amount)}</td>
                        <td className="td">
                          <p>{fmtDate(d.expected_close)}</p>
                          {d.overdue && <Badge status="critical" label="Overdue" />}
                        </td>
                        <td className="td">
                          <p>{d.last_activity_date ? fmtDate(d.last_activity_date) : <span className="text-muted">None yet</span>}</p>
                          {d.stalled && <Badge status="warning" label={`Quiet for ${d.days_idle} days`} />}
                        </td>
                        <td className="td text-right">{d.days_open} days</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </SectionCard>
        </>
      )}
    </div>
  );
}
