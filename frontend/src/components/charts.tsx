"use client";

import Link from "next/link";
import { useState } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { TrendPoint } from "@/lib/api";
import { fmtLongMonth, fmtMonth, inr, inrAxis, inrCompact, pct } from "@/lib/format";

interface Row {
  period: string;
  revenue: number;
  target: number | null;
  orders: number;
  partial: boolean;
}

function LegendKey({ kind, label }: { kind: "bar" | "line"; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink-2">
      {kind === "bar" ? (
        <span className="h-2.5 w-2.5 rounded-sm" style={{ background: "var(--series-1)" }} />
      ) : (
        <span className="relative h-2.5 w-4">
          <span className="absolute left-0 right-0 top-1/2 h-0.5 -translate-y-1/2 rounded" style={{ background: "var(--series-2)" }} />
          <span className="absolute left-1/2 top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full" style={{ background: "var(--series-2)" }} />
        </span>
      )}
      {label}
    </span>
  );
}

function TrendTooltip({ active, payload }: { active?: boolean; payload?: { payload: Row }[] }) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  const achievement = row.target ? (row.revenue / row.target) * 100 : null;
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-medium text-ink">
        {fmtLongMonth(row.period)}
        {row.partial && <span className="ml-1 font-normal text-muted">(so far)</span>}
      </p>
      <p className="flex items-center justify-between gap-6 text-ink-2">
        <LegendKey kind="bar" label="Revenue" />
        <span className="tabular font-medium text-ink">{inr(row.revenue)}</span>
      </p>
      {row.target !== null && (
        <p className="flex items-center justify-between gap-6 text-ink-2">
          <LegendKey kind="line" label="Target" />
          <span className="tabular font-medium text-ink">{inr(row.target)}</span>
        </p>
      )}
      <p className="mt-1 text-muted">
        {row.orders.toLocaleString("en-IN")} orders{achievement !== null && ` · ${pct(achievement)} of target`}
      </p>
    </div>
  );
}

/** Monthly revenue (columns) against target (line) on one shared rupee axis. */
export function RevenueTrendChart({ points, periodEnd }: { points: TrendPoint[]; periodEnd: string }) {
  const [showTable, setShowTable] = useState(false);
  const endMonth = periodEnd.slice(0, 7);
  const lastDay = new Date(Date.UTC(Number(periodEnd.slice(0, 4)), Number(periodEnd.slice(5, 7)), 0)).getUTCDate();
  const endIsPartial = Number(periodEnd.slice(8, 10)) < lastDay;

  const rows: Row[] = points.map((p) => ({
    period: p.period,
    revenue: Number(p.revenue),
    target: p.target === null ? null : Number(p.target),
    orders: p.orders,
    partial: endIsPartial && p.period.startsWith(endMonth),
  }));
  const hasTarget = rows.some((r) => r.target !== null);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-4">
          <LegendKey kind="bar" label="Revenue" />
          {hasTarget && <LegendKey kind="line" label="Target" />}
        </div>
        <button className="text-xs font-medium text-accent hover:underline" onClick={() => setShowTable((s) => !s)}>
          {showTable ? "Show chart" : "Show table"}
        </button>
      </div>

      {showTable ? (
        <div className="max-h-72 overflow-auto">
          <table className="w-full">
            <thead>
              <tr>
                <th className="th">Month</th>
                <th className="th text-right">Revenue</th>
                <th className="th text-right">Target</th>
                <th className="th text-right">Achievement</th>
                <th className="th text-right">Orders</th>
              </tr>
            </thead>
            <tbody className="tabular">
              {rows.map((r) => (
                <tr key={r.period}>
                  <td className="td">
                    {fmtLongMonth(r.period)}
                    {r.partial && <span className="text-muted"> (so far)</span>}
                  </td>
                  <td className="td text-right">{inr(r.revenue)}</td>
                  <td className="td text-right">{inr(r.target)}</td>
                  <td className="td text-right">{r.target ? pct((r.revenue / r.target) * 100) : "—"}</td>
                  <td className="td text-right">{r.orders.toLocaleString("en-IN")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="h-72" role="img" aria-label="Monthly revenue against target. Use Show table for the values.">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--grid)" strokeWidth={1} />
              <XAxis
                dataKey="period"
                tickFormatter={fmtMonth}
                tickLine={false}
                axisLine={{ stroke: "var(--axis)" }}
                tick={{ fill: "var(--muted)", fontSize: 12 }}
                interval="preserveStartEnd"
                minTickGap={12}
              />
              <YAxis
                tickFormatter={inrAxis}
                tickLine={false}
                axisLine={false}
                tick={{ fill: "var(--muted)", fontSize: 12 }}
                width={52}
              />
              <Tooltip content={<TrendTooltip />} cursor={{ fill: "var(--surface-2)" }} isAnimationActive={false} />
              <Bar dataKey="revenue" fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={24} isAnimationActive={false} />
              {hasTarget && (
                <Line
                  dataKey="target"
                  stroke="var(--series-2)"
                  strokeWidth={2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  dot={{ r: 4, fill: "var(--series-2)", stroke: "var(--surface)", strokeWidth: 2 }}
                  activeDot={{ r: 5, fill: "var(--series-2)", stroke: "var(--surface)", strokeWidth: 2 }}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              )}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

/** Ranked list with a thin magnitude bar per row; value printed at the bar's tip column. */
export function RankedBars({
  rows,
}: {
  rows: { key: string; label: string; detail?: string; value: number; share: number | null; href?: string }[];
}) {
  const max = Math.max(...rows.map((r) => r.value), 1);
  return (
    <ol className="space-y-3">
      {rows.map((r, i) => (
        <li key={r.key} className="group" title={`${r.label}: ${inr(r.value)}${r.share !== null ? ` (${pct(r.share)} of revenue)` : ""}`}>
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="min-w-0 truncate">
              <span className="tabular mr-2 text-muted">{i + 1}</span>
              {r.href ? (
                <Link href={r.href} className="font-medium hover:text-accent hover:underline">
                  {r.label}
                </Link>
              ) : (
                <span className="font-medium">{r.label}</span>
              )}
              {r.detail && <span className="ml-2 text-xs text-muted">{r.detail}</span>}
            </span>
            <span className="tabular shrink-0 font-medium">
              {inrCompact(r.value)}
              {r.share !== null && <span className="ml-1.5 text-xs font-normal text-muted">{pct(r.share)}</span>}
            </span>
          </div>
          <div className="mt-1.5 h-2 rounded-full bg-surface-2">
            <div className="h-full rounded-full transition-[filter] group-hover:brightness-110" style={{ width: `${(r.value / max) * 100}%`, background: "var(--series-1)" }} />
          </div>
        </li>
      ))}
    </ol>
  );
}
