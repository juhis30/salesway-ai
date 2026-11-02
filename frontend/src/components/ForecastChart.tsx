"use client";

import { useState } from "react";
import { CartesianGrid, ComposedChart, ErrorBar, Line, ReferenceLine, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from "recharts";
import type { MonthEndForecast } from "@/lib/api";
import { fmtDate, inr, inrAxis } from "@/lib/format";

interface Row {
  day: number;
  date: string;
  actual: number | null;
  forecast: number | null;
  range: [number, number] | null; // distances below and above the forecast
}

function Key({ kind, label }: { kind: "line" | "range" | "target"; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink-2">
      {kind === "line" && <span className="h-0.5 w-4 rounded" style={{ background: "var(--series-1)" }} />}
      {kind === "range" && (
        <span className="relative h-3 w-3">
          <span className="absolute inset-x-1/2 inset-y-0 w-px -translate-x-1/2" style={{ background: "var(--series-1)" }} />
          <span className="absolute left-1/2 top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full" style={{ background: "var(--series-1)" }} />
        </span>
      )}
      {kind === "target" && <span className="h-0.5 w-4 rounded" style={{ background: "var(--ink-2)" }} />}
      {label}
    </span>
  );
}

function ChartTooltip({ active, payload }: { active?: boolean; payload?: { payload: Row }[] }) {
  if (!active || !payload?.length) return null;
  const r = payload[0].payload;
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-medium">{fmtDate(r.date)}</p>
      {r.actual !== null && <p className="text-ink-2">Sales so far: <span className="tabular font-medium text-ink">{inr(r.actual)}</span></p>}
      {r.forecast !== null && r.range && (
        <>
          <p className="text-ink-2">Forecast: <span className="tabular font-medium text-ink">{inr(r.forecast)}</span></p>
          <p className="text-muted">Likely {inr(r.forecast - r.range[0])} – {inr(r.forecast + r.range[1])}</p>
        </>
      )}
    </div>
  );
}

/** Cumulative sales this month, the target as a reference line, and the month-end forecast with its range. */
export function ForecastChart({ data }: { data: MonthEndForecast }) {
  const [showTable, setShowTable] = useState(false);
  const lastDay = Number(data.month_end!.slice(8, 10));
  const actualByDay = new Map(data.daily.map((d) => [Number(d.date.slice(8, 10)), d.cumulative]));
  const rows: Row[] = Array.from({ length: lastDay }, (_, i) => {
    const day = i + 1;
    const isEnd = day === lastDay;
    return {
      day,
      date: `${data.month!.slice(0, 8)}${String(day).padStart(2, "0")}`,
      actual: actualByDay.get(day) ?? null,
      forecast: isEnd ? data.forecast : null,
      range: isEnd && data.forecast !== null ? [data.forecast - (data.lower ?? data.forecast), (data.upper ?? data.forecast) - data.forecast] : null,
    };
  });
  const top = Math.max(data.upper ?? 0, data.target ?? 0, data.forecast ?? 0) * 1.08;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-4">
          <Key kind="line" label="Sales so far" />
          <Key kind="range" label="Month-end forecast and likely range" />
          {data.target !== null && <Key kind="target" label="Target" />}
        </div>
        <button className="text-xs font-medium text-accent hover:underline" onClick={() => setShowTable((s) => !s)}>
          {showTable ? "Show chart" : "Show table"}
        </button>
      </div>
      {showTable ? (
        <table className="w-full">
          <tbody className="tabular">
            <tr><td className="td">Sales so far ({fmtDate(data.as_of)})</td><td className="td text-right">{inr(data.so_far)}</td></tr>
            <tr><td className="td">Forecast for {fmtDate(data.month_end)}</td><td className="td text-right">{inr(data.forecast)}</td></tr>
            <tr><td className="td">Likely range (8 in 10 past outcomes)</td><td className="td text-right">{inr(data.lower)} – {inr(data.upper)}</td></tr>
            <tr><td className="td">Target</td><td className="td text-right">{inr(data.target)}</td></tr>
          </tbody>
        </table>
      ) : (
        <div className="h-72" role="img" aria-label={`Sales so far ${inr(data.so_far)}; forecast ${inr(data.forecast)}, likely ${inr(data.lower)} to ${inr(data.upper)}; target ${inr(data.target)}.`}>
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={rows} margin={{ top: 12, right: 16, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--grid)" />
              <XAxis dataKey="day" tickLine={false} axisLine={{ stroke: "var(--axis)" }} tick={{ fill: "var(--muted)", fontSize: 12 }}
                     ticks={[1, 8, 15, 22, lastDay]} tickFormatter={(d: number) => `${d}`} />
              <YAxis domain={[0, top]} tickFormatter={inrAxis} tickLine={false} axisLine={false} tick={{ fill: "var(--muted)", fontSize: 12 }} width={56} />
              <Tooltip content={<ChartTooltip />} cursor={{ stroke: "var(--axis)" }} isAnimationActive={false} />
              {data.target !== null && (
                <ReferenceLine y={data.target} stroke="var(--ink-2)" strokeWidth={1.5}
                               label={{ value: `Target ${inrAxis(data.target)}`, position: "insideTopLeft", fill: "var(--ink-2)", fontSize: 12 }} />
              )}
              <Line dataKey="actual" stroke="var(--series-1)" strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false} />
              <Scatter dataKey="forecast" fill="var(--series-1)" stroke="var(--surface)" strokeWidth={2} isAnimationActive={false}>
                <ErrorBar dataKey="range" direction="y" width={10} stroke="var(--series-1)" strokeWidth={2} />
              </Scatter>
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      <p className="mt-1 text-xs text-muted">Day of month</p>
    </div>
  );
}
