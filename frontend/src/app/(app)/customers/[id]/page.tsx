"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { RevenueTrendChart } from "@/components/charts";
import { presetRange, todayIn, type Preset } from "@/components/Filters";
import { Pager } from "@/components/table";
import { Empty, ErrorBox, SectionCard, Skeleton, StatTile, useApi } from "@/components/ui";
import type { CustomerDetail, Order, Overview, Page, TopProduct, TrendPoint } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtRange, inr, inrCompact, number, pct } from "@/lib/format";

type Period = "all" | Exclude<Preset, "custom">;

const PERIODS: { value: Period; label: string }[] = [
  { value: "all", label: "All time" },
  { value: "this_month", label: "This month" },
  { value: "last_3_months", label: "Last 3 full months" },
  { value: "last_12_months", label: "Last 12 full months" },
  { value: "this_fy", label: "Financial year to date" },
];

const STATUS_LABELS: Record<Order["status"], string> = {
  pending: "Pending",
  confirmed: "Confirmed",
  completed: "Completed",
  cancelled: "Cancelled",
  returned: "Returned",
};

function daysBetween(fromIso: string, toIso: string): number {
  return Math.round((Date.parse(toIso) - Date.parse(fromIso)) / 86_400_000);
}

export default function CustomerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { me } = useAuth();
  const today = todayIn(me!.organization.timezone);
  const [period, setPeriod] = useState<Period>("all");

  const customer = useApi<CustomerDetail>(`/customers/${id}`);
  const c = customer.data;

  // "All time" runs from the customer's first order; other periods use the shared presets.
  const range = useMemo<[string, string] | null>(() => {
    if (period !== "all") return presetRange(period, today);
    return c?.first_order_date ? [c.first_order_date, today] : null;
  }, [period, today, c?.first_order_date]);

  const query = range ? { customer_id: id, start: range[0], end: range[1] } : undefined;
  const ready = c !== null && range !== null;
  const overview = useApi<Overview>(ready ? "/analytics/overview" : null, query);
  const trend = useApi<TrendPoint[]>(ready ? "/analytics/trend" : null, { ...query, granularity: "month" });
  const products = useApi<TopProduct[]>(ready ? "/analytics/top-products" : null, { ...query, limit: 100 });

  if (customer.error) {
    return (
      <div className="space-y-4">
        <BackLink />
        <ErrorBox message={customer.error} onRetry={customer.reload} />
      </div>
    );
  }
  if (!c) return <Skeleton className="h-96" />;

  const daysSince = c.last_order_date ? daysBetween(c.last_order_date, today) : null;

  return (
    <div className="space-y-6">
      <BackLink />

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">{c.name}</h1>
          <p className="mt-1 text-sm text-ink-2">
            {[c.code, c.segment, c.city].filter(Boolean).join(" · ")}
          </p>
          <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-sm">
            <Meta label="Salesperson" value={c.owner_name} />
            <Meta label="Territory" value={c.territory_name} />
            <Meta label="Phone" value={c.phone} />
            <Meta label="Customer since" value={c.first_order_date && fmtDate(c.first_order_date)} />
          </dl>
        </div>
        <div className="card px-4 py-3 text-sm">
          <p className="text-ink-2">Last order</p>
          <p className="font-semibold">{fmtDate(c.last_order_date)}</p>
          {daysSince !== null && <p className="text-xs text-muted">{daysSince === 0 ? "today" : `${daysSince} days ago`}</p>}
        </div>
      </header>

      {c.lifetime_orders === 0 ? (
        <div className="card">
          <Empty title="No orders yet" hint="This customer hasn't placed an order that counts as revenue." />
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col">
              <span className="label">Period</span>
              <select className="input py-1.5" value={period} onChange={(e) => setPeriod(e.target.value as Period)}>
                {PERIODS.map((p) => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </label>
            {range && <p className="pb-2 text-sm text-ink-2">{fmtRange(range[0], range[1])}</p>}
          </div>

          {overview.error ? (
            <ErrorBox message={overview.error} onRetry={overview.reload} />
          ) : !overview.data ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-28" />)}
            </div>
          ) : (
            <div className={`grid gap-4 sm:grid-cols-2 lg:grid-cols-4 ${overview.loading ? "opacity-60" : ""}`}>
              <StatTile label="Revenue" value={inrCompact(overview.data.revenue)} title={inr(overview.data.revenue)} />
              <StatTile label="Orders" value={number(overview.data.orders)} />
              <StatTile label="Average order value" value={inrCompact(overview.data.average_order_value)} title={inr(overview.data.average_order_value)} />
              <StatTile label="Different products bought" value={products.data ? number(products.data.length) : "…"} />
            </div>
          )}

          <SectionCard title="What they bought" subtitle="Every product in the period, by revenue. Select a column heading to sort.">
            {products.error ? (
              <ErrorBox message={products.error} onRetry={products.reload} />
            ) : !products.data ? (
              <Skeleton className="h-64" />
            ) : products.data.length === 0 ? (
              <Empty title="No purchases in this period" hint="Try a longer period." />
            ) : (
              <ProductsTable rows={products.data} />
            )}
          </SectionCard>

          <SectionCard title="Revenue by month" subtitle="Net of tax; cancelled, returned and pending orders excluded.">
            {trend.error ? (
              <ErrorBox message={trend.error} onRetry={trend.reload} />
            ) : !trend.data || !range ? (
              <Skeleton className="h-72" />
            ) : trend.data.length === 0 ? (
              <Empty title="No purchases in this period" />
            ) : (
              <RevenueTrendChart points={trend.data} periodEnd={range[1]} />
            )}
          </SectionCard>

          {range && <OrderHistory customerId={id} start={range[0]} end={range[1]} />}
        </>
      )}
    </div>
  );
}

function BackLink() {
  return (
    <Link href="/customers" className="inline-flex items-center gap-1 text-sm text-ink-2 hover:text-ink">
      ← Customers
    </Link>
  );
}

function Meta({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="flex gap-1.5">
      <dt className="text-muted">{label}</dt>
      <dd className="font-medium">{value || "—"}</dd>
    </div>
  );
}

type SortKey = "revenue" | "units" | "orders" | "last_order_date" | "name";

function ProductsTable({ rows }: { rows: TopProduct[] }) {
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "revenue", desc: true });
  const max = Math.max(...rows.map((r) => Number(r.revenue)), 1);

  const sorted = useMemo(() => {
    const value = (r: TopProduct): number | string =>
      sort.key === "name" ? r.name : sort.key === "last_order_date" ? r.last_order_date : Number(r[sort.key]);
    return [...rows].sort((a, b) => {
      const [x, y] = [value(a), value(b)];
      const cmp = x < y ? -1 : x > y ? 1 : 0;
      return sort.desc ? -cmp : cmp;
    });
  }, [rows, sort]);

  const header = (key: SortKey, label: string, align = "text-right") => (
    <th className={`th ${align}`} aria-sort={sort.key === key ? (sort.desc ? "descending" : "ascending") : undefined}>
      <button
        className="inline-flex items-center gap-1 hover:text-ink"
        onClick={() => setSort((s) => ({ key, desc: s.key === key ? !s.desc : key !== "name" }))}
      >
        {label}
        {sort.key === key && <span aria-hidden>{sort.desc ? "↓" : "↑"}</span>}
      </button>
    </th>
  );

  return (
    <div className="-mx-4 overflow-x-auto">
      <table className="w-full min-w-[760px]">
        <thead>
          <tr>
            {header("name", "Product", "text-left")}
            {header("units", "Units")}
            {header("orders", "Orders")}
            <th className="th text-right">Avg price</th>
            {header("revenue", "Revenue")}
            <th className="th w-40">Share</th>
            {header("last_order_date", "Last bought")}
          </tr>
        </thead>
        <tbody className="tabular">
          {sorted.map((p) => (
            <tr key={p.product_id} className="hover:bg-surface-2">
              <td className="td">
                <p className="font-medium">{p.name}</p>
                <p className="text-xs text-muted">
                  {p.sku}
                  {p.category && ` · ${p.category}`}
                </p>
              </td>
              <td className="td text-right">{number(p.units)}</td>
              <td className="td text-right">{number(p.orders)}</td>
              <td className="td text-right">{inr(p.average_unit_price)}</td>
              <td className="td text-right font-medium">{inr(p.revenue)}</td>
              <td className="td">
                <div className="flex items-center gap-2" title={`${pct(p.share_pct)} of this customer's revenue`}>
                  <div className="h-2 flex-1 rounded-full bg-surface-2">
                    <div className="h-full rounded-full" style={{ width: `${(Number(p.revenue) / max) * 100}%`, background: "var(--series-1)" }} />
                  </div>
                  <span className="w-12 text-right text-xs text-ink-2">{pct(p.share_pct)}</span>
                </div>
              </td>
              <td className="td text-right">{fmtDate(p.last_order_date)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OrderHistory({ customerId, start, end }: { customerId: string; start: string; end: string }) {
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<Set<string>>(new Set());
  useEffect(() => {
    setOffset(0);
  }, [start, end]);

  const orders = useApi<Page<Order>>(`/customers/${customerId}/orders`, { start, end, limit: 25, offset });

  const toggle = (orderId: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(orderId)) next.delete(orderId);
      else next.add(orderId);
      return next;
    });

  return (
    <SectionCard title="Orders" subtitle="All orders in the period, newest first. Select an order to see its items.">
      {orders.error ? (
        <ErrorBox message={orders.error} onRetry={orders.reload} />
      ) : !orders.data ? (
        <Skeleton className="h-48" />
      ) : orders.data.items.length === 0 ? (
        <Empty title="No orders in this period" />
      ) : (
        <div className="-mx-4 -mb-4">
          <ul className={orders.loading ? "opacity-60" : ""}>
            {orders.data.items.map((o) => {
              const expanded = open.has(o.id);
              return (
                <li key={o.id} className="border-t border-line">
                  <button
                    className="grid w-full grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 px-4 py-3 text-left text-sm hover:bg-surface-2 sm:grid-cols-[7rem_1fr_8rem_8rem_1.5rem]"
                    aria-expanded={expanded}
                    onClick={() => toggle(o.id)}
                  >
                    <span className="tabular text-ink-2">{fmtDate(o.order_date)}</span>
                    <span className="min-w-0">
                      <span className="font-medium">{o.order_number}</span>
                      <span className="ml-2 text-xs text-muted">
                        {o.lines.length} item{o.lines.length === 1 ? "" : "s"}
                        {o.salesperson_name && ` · ${o.salesperson_name}`}
                      </span>
                    </span>
                    <span className={`text-xs ${o.counts_as_revenue ? "text-ink-2" : "text-muted"}`}>
                      {STATUS_LABELS[o.status]}
                      {!o.counts_as_revenue && " · not counted"}
                    </span>
                    <span className={`tabular text-right font-medium ${o.counts_as_revenue ? "" : "text-muted line-through"}`}>
                      {inr(o.net_amount)}
                    </span>
                    <span className="hidden text-muted sm:block" aria-hidden>{expanded ? "▾" : "▸"}</span>
                  </button>
                  {expanded && (
                    <div className="overflow-x-auto bg-surface-2/50 px-4 pb-3">
                      <table className="w-full min-w-[520px]">
                        <thead>
                          <tr>
                            <th className="th pl-0">Product</th>
                            <th className="th text-right">Qty</th>
                            <th className="th text-right">Price</th>
                            <th className="th pr-0 text-right">Amount</th>
                          </tr>
                        </thead>
                        <tbody className="tabular">
                          {o.lines.map((l) => (
                            <tr key={l.product_id}>
                              <td className="td pl-0">
                                {l.product_name} <span className="text-xs text-muted">{l.sku}</span>
                              </td>
                              <td className="td text-right">{number(l.quantity)}</td>
                              <td className="td text-right">{inr(l.unit_price)}</td>
                              <td className="td pr-0 text-right">{inr(l.line_amount)}</td>
                            </tr>
                          ))}
                          <tr>
                            <td className="td pl-0 text-ink-2" colSpan={3}>Net (excl. tax) · tax {inr(o.tax_amount)} · total {inr(o.total_amount)}</td>
                            <td className="td pr-0 text-right font-semibold">{inr(o.net_amount)}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          <Pager total={orders.data.total} offset={offset} onChange={setOffset} />
        </div>
      )}
    </SectionCard>
  );
}
