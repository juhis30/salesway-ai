"use client";

import { RankedBars, RevenueTrendChart } from "@/components/charts";
import { FilterBar, monthStart, useDashboardFilters } from "@/components/Filters";
import {
  Delta,
  Empty,
  ErrorBox,
  Meter,
  SectionCard,
  Skeleton,
  StatTile,
  StatusBadge,
  StatusIcon,
  useApi,
  type Status,
} from "@/components/ui";
import Link from "next/link";
import type { CustomerHealth, FollowUp, LeaderboardRow, Overview, Pipeline, TopCustomer, TopProduct, TrendPoint } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtRange, inr, inrCompact, number, pct } from "@/lib/format";

export default function DashboardPage() {
  const { me } = useAuth();
  const { filters, update, query, today } = useDashboardFilters(me!.organization.timezone);

  const overview = useApi<Overview>("/analytics/overview", query);
  // The trend always shows the 12 months ending with the selected period, with the same filters.
  const trend = useApi<TrendPoint[]>("/analytics/trend", { ...query, granularity: "month", start: monthStart(query.end, -11) });
  const board = useApi<LeaderboardRow[]>("/analytics/leaderboard", query);
  const customers = useApi<TopCustomer[]>("/analytics/top-customers", { ...query, limit: 8 });
  const products = useApi<TopProduct[]>("/analytics/top-products", { ...query, limit: 8 });

  const firstName = me!.user.full_name.split(" ")[0];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Hello, {firstName}</h1>
        <p className="mt-1 text-sm text-ink-2">
          {fmtRange(filters.start, filters.end)}
          {overview.data?.data_as_of && <> · Data up to {fmtDate(overview.data.data_as_of)}</>}
        </p>
      </div>

      <FilterBar filters={filters} update={update} today={today} />

      {overview.error ? (
        <ErrorBox message={overview.error} onRetry={overview.reload} />
      ) : (
        <KpiRow data={overview.data} loading={overview.loading} />
      )}

      <NeedsAttention query={query} />

      <SectionCard
        title="Revenue vs target by month"
        subtitle={`12 months to ${fmtDate(filters.end)}. Revenue is net of tax; cancelled, returned and pending orders are excluded.`}
      >
        {trend.error ? (
          <ErrorBox message={trend.error} onRetry={trend.reload} />
        ) : trend.loading && !trend.data ? (
          <Skeleton className="h-72" />
        ) : trend.data?.length ? (
          <RevenueTrendChart points={trend.data} periodEnd={filters.end} />
        ) : (
          <Empty title="No sales in this period" hint="Try a longer period or clear the filters." />
        )}
      </SectionCard>

      {(board.loading || (board.data?.length ?? 0) > 1) && (
        <SectionCard title="Salesperson leaderboard" subtitle="Select a name to see their numbers.">
          {board.error ? (
            <ErrorBox message={board.error} onRetry={board.reload} />
          ) : board.loading && !board.data ? (
            <Skeleton className="h-48" />
          ) : (
            <Leaderboard rows={board.data!} onSelect={(id) => update({ salesperson_id: id })} />
          )}
        </SectionCard>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <SectionCard title="Top customers" subtitle="By revenue in the period" className="min-w-0">
          {customers.error ? (
            <ErrorBox message={customers.error} onRetry={customers.reload} />
          ) : customers.loading && !customers.data ? (
            <Skeleton className="h-64" />
          ) : customers.data?.length ? (
            <RankedBars
              rows={customers.data.map((c) => ({
                key: c.customer_id,
                label: c.name,
                detail: `${c.segment ?? ""} · ${c.orders} orders · last ${fmtDate(c.last_order_date)}`,
                value: Number(c.revenue),
                share: c.share_pct,
                href: `/customers/${c.customer_id}`,
              }))}
            />
          ) : (
            <Empty title="No customer purchases in this period" />
          )}
        </SectionCard>

        <SectionCard title="Top products" subtitle="By revenue in the period" className="min-w-0">
          {products.error ? (
            <ErrorBox message={products.error} onRetry={products.reload} />
          ) : products.loading && !products.data ? (
            <Skeleton className="h-64" />
          ) : products.data?.length ? (
            <RankedBars
              rows={products.data.map((p) => ({
                key: p.product_id,
                label: p.name,
                detail: `${number(p.units)} units`,
                value: Number(p.revenue),
                share: p.share_pct,
              }))}
            />
          ) : (
            <Empty title="No product sales in this period" />
          )}
        </SectionCard>
      </div>
    </div>
  );
}

function NeedsAttention({ query }: { query: Record<string, string> }) {
  const { product_id: _ignored, ...q } = query;
  const health = useApi<CustomerHealth>("/analytics/customer-health", { ...q, limit: 1 });
  const followUps = useApi<FollowUp[]>("/activities/follow-ups", { ...q, when: "overdue", limit: 500 });
  const pipeline = useApi<Pipeline>("/analytics/pipeline", q);

  const card = (href: string, label: string, value: string | null, detail: React.ReactNode, status: Status) => (
    <Link href={href} className="card group flex items-start gap-3 p-4 transition hover:bg-surface-2">
      {value === null ? <span className="h-4 w-4 shrink-0 animate-pulse rounded-full bg-surface-2" aria-hidden /> : <StatusIcon status={status} />}
      <div className="min-w-0 flex-1">
        <p className="text-sm text-ink-2">{label}</p>
        <p className="text-xl font-semibold">{value ?? "…"}</p>
        <p className="text-xs text-muted">{detail}</p>
      </div>
      <span className="text-muted group-hover:text-ink" aria-hidden>→</span>
    </Link>
  );

  const s = health.data?.summary;
  const quiet = s ? s.at_risk + s.dormant : null;
  const overdue = followUps.data?.length ?? null;
  const stalled = pipeline.data?.stalled_count ?? null;

  return (
    <section aria-label="Needs attention" className="grid gap-4 md:grid-cols-3">
      {card(
        "/insights/customers",
        "Customers gone quiet",
        quiet === null ? null : number(quiet),
        s ? `${s.at_risk} at risk · ${s.dormant} dormant · ${inrCompact(s.monthly_value_at_risk)}/month at stake` : "",
        quiet ? "warning" : "good",
      )}
      {card(
        "/activity",
        "Overdue follow-ups",
        overdue === null ? null : number(overdue),
        overdue ? "visits and calls promised but not done" : "nothing overdue",
        overdue ? "critical" : "good",
      )}
      {card(
        "/pipeline",
        "Stalled deals",
        stalled === null ? null : number(stalled),
        pipeline.data ? `of ${pipeline.data.open_count} open · ${inrCompact(pipeline.data.open_value)} pipeline` : "",
        stalled ? "warning" : "good",
      )}
    </section>
  );
}

function KpiRow({ data, loading }: { data: Overview | null; loading: boolean }) {
  if (!data) {
    return (
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className={`h-32 ${i === 0 ? "sm:col-span-2" : ""}`} />
        ))}
      </div>
    );
  }
  const vs = fmtRange(data.comparison_period.start, data.comparison_period.end);

  return (
    <div className={`grid gap-4 sm:grid-cols-2 lg:grid-cols-4 ${loading ? "opacity-60 transition-opacity" : ""}`}>
      <div className="sm:col-span-2">
        <StatTile
          hero
          label="Revenue"
          value={inrCompact(data.revenue)}
          title={inr(data.revenue)}
          footer={<Delta value={data.revenue_growth_pct} vs={vs} />}
        />
      </div>

      <div className="card flex flex-col gap-1 p-4 sm:col-span-2">
        <div className="flex items-start justify-between gap-2">
          <span className="text-sm text-ink-2">Target achievement</span>
          <StatusBadge pct={data.achievement_pct} />
        </div>
        {data.target === null ? (
          <p className="mt-2 text-sm text-ink-2">
            Targets are set per salesperson per month, so they aren&apos;t shown when filtering by product, segment or territory.
          </p>
        ) : (
          <>
            <span className="text-2xl font-semibold">
              {pct(data.achievement_pct)} <span className="text-base font-normal text-ink-2">of {inrCompact(data.target)}</span>
            </span>
            <Meter pct={data.achievement_pct} className="mt-1" />
            <p className="mt-1 text-xs text-ink-2">
              {Number(data.target_gap) > 0 ? (
                <>
                  {inrCompact(data.target_gap)} to go
                  {data.required_daily_run_rate && <> · {inrCompact(data.required_daily_run_rate)} needed per day for the rest of the month</>}
                </>
              ) : (
                <>Target exceeded by {inrCompact(-Number(data.target_gap))}</>
              )}
            </p>
          </>
        )}
      </div>

      <StatTile label="Orders" value={number(data.orders)} footer={<Delta value={data.orders_growth_pct} vs={vs} />} />
      <StatTile label="Average order value" value={inrCompact(data.average_order_value)} title={inr(data.average_order_value)} />
      <StatTile label="Buying customers" value={number(data.active_customers)} footer={<span className="text-xs text-muted">placed at least one order</span>} />
      <StatTile label="Last period revenue" value={inrCompact(data.previous_revenue)} footer={<span className="text-xs text-muted">{vs}</span>} />
    </div>
  );
}

function Leaderboard({ rows, onSelect }: { rows: LeaderboardRow[]; onSelect: (id: string) => void }) {
  return (
    <div className="-mx-4 overflow-x-auto">
      <table className="w-full min-w-[640px]">
        <thead>
          <tr>
            <th className="th w-10">#</th>
            <th className="th">Salesperson</th>
            <th className="th text-right">Revenue</th>
            <th className="th text-right">Orders</th>
            <th className="th text-right">Target</th>
            <th className="th w-56">Achievement</th>
          </tr>
        </thead>
        <tbody className="tabular">
          {rows.map((r) => (
            <tr key={r.salesperson_id} className="hover:bg-surface-2">
              <td className="td text-muted">{r.rank}</td>
              <td className="td">
                <button className="font-medium hover:text-accent hover:underline" onClick={() => onSelect(r.salesperson_id)}>
                  {r.name}
                </button>
              </td>
              <td className="td text-right">{inr(r.revenue)}</td>
              <td className="td text-right">{number(r.orders)}</td>
              <td className="td text-right">{inr(r.target)}</td>
              <td className="td">
                <div className="flex items-center gap-3">
                  <Meter pct={r.achievement_pct} className="w-20" />
                  <span className="w-14 text-right">{pct(r.achievement_pct)}</span>
                  <StatusBadge pct={r.achievement_pct} />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
