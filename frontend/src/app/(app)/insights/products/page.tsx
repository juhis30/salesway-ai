"use client";

import { useMemo, useState } from "react";
import { FilterBar, useDashboardFilters } from "@/components/Filters";
import { Empty, ErrorBox, SectionCard, Skeleton, useApi } from "@/components/ui";
import type { ProductTrend, ProductTrends } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtRange, inr, number, pct, signedPct } from "@/lib/format";

const TREND: Record<ProductTrend, { glyph: string; label: string; className: string }> = {
  rising: { glyph: "▲", label: "Rising", className: "text-up" },
  declining: { glyph: "▼", label: "Declining", className: "text-down" },
  steady: { glyph: "■", label: "Steady", className: "text-ink-2" },
  new: { glyph: "✦", label: "New", className: "text-ink-2" },
  stopped: { glyph: "▼", label: "Stopped selling", className: "text-down" },
};

const GROWTH_SCALE = 100; // bars are drawn on a ±100% scale; larger values are capped and labelled

/** Diverging bar from a zero line: blue for growth, red for decline. */
function GrowthBar({ value }: { value: number | null }) {
  if (value === null) return <span className="text-xs text-muted">—</span>;
  const width = Math.min(Math.abs(value), GROWTH_SCALE) / GROWTH_SCALE * 50;
  return (
    <div className="flex items-center gap-2">
      <div className="relative h-3 flex-1" aria-hidden>
        <div className="absolute inset-y-0 left-1/2 w-px bg-[var(--axis)]" />
        <div
          className={`absolute inset-y-0.5 ${value >= 0 ? "rounded-r" : "rounded-l"}`}
          style={{
            width: `${width}%`,
            left: value >= 0 ? "50%" : `${50 - width}%`,
            background: value >= 0 ? "var(--series-1)" : "var(--down)",
          }}
        />
      </div>
      <span className={`w-16 text-right text-sm font-medium ${value > 0 ? "text-up" : value < 0 ? "text-down" : "text-ink-2"}`}>
        {signedPct(value)}
      </span>
    </div>
  );
}

type SortKey = "revenue" | "growth_pct";

export default function ProductTrendsPage() {
  const { me } = useAuth();
  const { filters, update, query, today } = useDashboardFilters(me!.organization.timezone, "last_3_months");
  const [category, setCategory] = useState("");
  const [trendFilter, setTrendFilter] = useState<ProductTrend | "">("");
  const [sort, setSort] = useState<SortKey>("revenue");

  const { product_id: _ignored, ...trendQuery } = query;
  const trends = useApi<ProductTrends>("/analytics/product-trends", trendQuery);
  const data = trends.data;

  const categories = useMemo(() => [...new Set((data?.products ?? []).map((p) => p.category).filter(Boolean))].sort() as string[], [data]);
  const rows = useMemo(() => {
    const list = (data?.products ?? []).filter((p) => (!category || p.category === category) && (!trendFilter || p.trend === trendFilter));
    return sort === "revenue"
      ? list
      : [...list].sort((a, b) => (b.growth_pct ?? -Infinity) - (a.growth_pct ?? -Infinity));
  }, [data, category, trendFilter, sort]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Product trends</h1>
        <p className="mt-1 text-sm text-ink-2">Which products are growing or slowing compared with the previous period.</p>
      </div>

      <FilterBar filters={filters} update={update} today={today} hide={["product_id"]} />

      {trends.error ? (
        <ErrorBox message={trends.error} onRetry={trends.reload} />
      ) : !data ? (
        <Skeleton className="h-96" />
      ) : (
        <SectionCard
          title={`${fmtRange(data.period.start, data.period.end)} vs ${fmtRange(data.comparison_period.start, data.comparison_period.end)}`}
          subtitle={
            !data.comparison_available
              ? "There's no data for the comparison period yet, so growth can't be shown. Try a later period."
              : !data.trends_available
                ? `This period is shorter than ${data.min_trend_days} days, so changes are shown but not labelled as trends: a few days of orders is too noisy.`
                : `Rising or declining means a change of ${data.threshold_pct}% or more in revenue.`
          }
        >
          <div className="mb-4 flex flex-wrap items-center gap-2">
            {(Object.keys(TREND) as ProductTrend[])
              .filter((t) => data.counts[t])
              .map((t) => (
                <button
                  key={t}
                  aria-pressed={trendFilter === t}
                  onClick={() => setTrendFilter(trendFilter === t ? "" : t)}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm ${
                    trendFilter === t ? "border-accent bg-accent-soft" : "border-line hover:bg-surface-2"
                  }`}
                >
                  <span className={TREND[t].className}>{TREND[t].glyph}</span>
                  {TREND[t].label} <span className="tabular text-muted">{data.counts[t]}</span>
                </button>
              ))}
            <span className="flex-1" />
            {categories.length > 1 && (
              <select className="input w-auto py-1" value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category">
                <option value="">All categories</option>
                {categories.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            )}
            <select className="input w-auto py-1" value={sort} onChange={(e) => setSort(e.target.value as SortKey)} aria-label="Sort by">
              <option value="revenue">Sort by revenue</option>
              <option value="growth_pct">Sort by growth</option>
            </select>
          </div>

          {rows.length === 0 ? (
            <Empty title="No products match" />
          ) : (
            <div className="-mx-4 overflow-x-auto">
              <table className="w-full min-w-[900px]">
                <thead>
                  <tr>
                    <th className="th">Product</th>
                    <th className="th text-right">Revenue</th>
                    <th className="th text-right">Before</th>
                    <th className="th w-64">Growth</th>
                    <th className="th text-right">Units</th>
                    <th className="th text-right">Buyers</th>
                    <th className="th">Trend</th>
                  </tr>
                </thead>
                <tbody className="tabular">
                  {rows.map((p) => (
                    <tr key={p.product_id} className="hover:bg-surface-2">
                      <td className="td">
                        <p className="font-medium">{p.name}</p>
                        <p className="text-xs text-muted">
                          {p.sku}
                          {p.category && ` · ${p.category}`}
                          {p.share_pct !== null && ` · ${pct(p.share_pct)} of revenue`}
                        </p>
                      </td>
                      <td className="td text-right font-medium">{inr(p.revenue)}</td>
                      <td className="td text-right text-ink-2">{data.comparison_available ? inr(p.previous_revenue) : "—"}</td>
                      <td className="td">
                        <GrowthBar value={p.growth_pct} />
                      </td>
                      <td className="td text-right">{number(p.units)}</td>
                      <td className="td text-right" title={`${p.previous_buyers} before`}>
                        {p.buyers}
                        {data.comparison_available && p.buyers !== p.previous_buyers && (
                          <span className="ml-1 text-xs text-muted">({p.buyers > p.previous_buyers ? "+" : "−"}{Math.abs(p.buyers - p.previous_buyers)})</span>
                        )}
                      </td>
                      <td className="td">
                        {p.trend && (
                          <span className="whitespace-nowrap text-sm">
                            <span className={TREND[p.trend].className}>{TREND[p.trend].glyph}</span> {TREND[p.trend].label}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </SectionCard>
      )}
    </div>
  );
}
