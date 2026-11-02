"use client";

import { useMemo, useState } from "react";
import type { Page, Product, Team, Territory, User } from "@/lib/api";
import { isOrgWide, useAuth } from "@/lib/auth";
import { useApi } from "./ui";

export type Preset = "this_month" | "last_month" | "last_3_months" | "this_fy" | "last_12_months" | "custom";

const PRESETS: { value: Preset; label: string }[] = [
  { value: "this_month", label: "This month" },
  { value: "last_month", label: "Last month" },
  { value: "last_3_months", label: "Last 3 full months" },
  { value: "this_fy", label: "Financial year to date" },
  { value: "last_12_months", label: "Last 12 full months" },
  { value: "custom", label: "Custom range" },
];

export interface DashboardFilters {
  preset: Preset;
  start: string;
  end: string;
  salesperson_id: string;
  team_id: string;
  territory_id: string;
  customer_segment: string;
  product_id: string;
}

const DIMENSIONS = ["salesperson_id", "team_id", "territory_id", "customer_segment", "product_id"] as const;

// --- Date helpers on plain YYYY-MM-DD strings (no timezone drift) ---------------------------

export function todayIn(timeZone: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone }).format(new Date());
}

function ymd(y: number, m: number, d: number): string {
  const date = new Date(Date.UTC(y, m, d));
  return date.toISOString().slice(0, 10);
}

export function monthStart(iso: string, offsetMonths = 0): string {
  const [y, m] = iso.split("-").map(Number);
  return ymd(y, m - 1 + offsetMonths, 1);
}

function monthEnd(iso: string): string {
  const [y, m] = iso.split("-").map(Number);
  return ymd(y, m, 0);
}

export function presetRange(preset: Exclude<Preset, "custom">, today: string): [string, string] {
  switch (preset) {
    case "this_month":
      return [monthStart(today), today];
    case "last_month": {
      const start = monthStart(today, -1);
      return [start, monthEnd(start)];
    }
    // Multi-month presets use complete months, so a part-month doesn't skew totals vs targets.
    case "last_3_months":
      return [monthStart(today, -3), monthEnd(monthStart(today, -1))];
    case "this_fy": {
      // Indian financial year: 1 April – 31 March.
      const [y, m] = today.split("-").map(Number);
      return [`${m >= 4 ? y : y - 1}-04-01`, today];
    }
    case "last_12_months":
      return [monthStart(today, -12), monthEnd(monthStart(today, -1))];
  }
}

export function useDashboardFilters(timeZone: string, initialPreset: Exclude<Preset, "custom"> = "this_month") {
  const today = todayIn(timeZone);
  const [filters, setFilters] = useState<DashboardFilters>(() => {
    const [start, end] = presetRange(initialPreset, today);
    return { preset: initialPreset, start, end, salesperson_id: "", team_id: "", territory_id: "", customer_segment: "", product_id: "" };
  });

  const update = (patch: Partial<DashboardFilters>) => {
    setFilters((current) => {
      const next = { ...current, ...patch };
      if (patch.preset && patch.preset !== "custom") [next.start, next.end] = presetRange(patch.preset, today);
      return next;
    });
  };

  const query = useMemo(() => {
    const { preset: _preset, ...rest } = filters;
    return rest;
  }, [filters]);

  return { filters, update, query, today };
}

// --- Filter bar ------------------------------------------------------------------------

function Select({
  label,
  value,
  onChange,
  options,
  allLabel,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  allLabel: string;
}) {
  return (
    <label className="flex min-w-[9rem] flex-1 flex-col sm:flex-none">
      <span className="label">{label}</span>
      <select className="input py-1.5" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{allLabel}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

type Dimension = (typeof DIMENSIONS)[number];

export function FilterBar({
  filters,
  update,
  today,
  hide = [],
  periodLabel = "Period",
}: {
  filters: DashboardFilters;
  update: (patch: Partial<DashboardFilters>) => void;
  today: string;
  /** Dimensions that don't apply to the page using the bar. */
  hide?: Dimension[];
  periodLabel?: string;
}) {
  const { me } = useAuth();
  const role = me!.user.role;
  const orgWide = isOrgWide(role);

  const users = useApi<User[]>("/users");
  const teams = useApi<Team[]>(orgWide ? "/teams" : null);
  const territories = useApi<Territory[]>(orgWide ? "/territories" : null);
  const segments = useApi<string[]>("/customers/segments");
  const products = useApi<Page<Product>>(hide.includes("product_id") ? null : "/products", { limit: 500 });

  const reps = (users.data ?? []).filter((u) => u.role === "sales_executive");
  const activeDimensions = DIMENSIONS.filter((d) => filters[d]).length;

  return (
    <div className="card flex flex-wrap items-end gap-3 p-3">
      <label className="flex min-w-[10rem] flex-1 flex-col sm:flex-none">
        <span className="label">{periodLabel}</span>
        <select className="input py-1.5" value={filters.preset} onChange={(e) => update({ preset: e.target.value as Preset })}>
          {PRESETS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </label>
      {filters.preset === "custom" && (
        <>
          <label className="flex flex-col">
            <span className="label">From</span>
            <input type="date" className="input py-1.5" value={filters.start} max={filters.end} onChange={(e) => e.target.value && update({ start: e.target.value })} />
          </label>
          <label className="flex flex-col">
            <span className="label">To</span>
            <input type="date" className="input py-1.5" value={filters.end} min={filters.start} max={today} onChange={(e) => e.target.value && update({ end: e.target.value })} />
          </label>
        </>
      )}

      {reps.length > 1 && !hide.includes("salesperson_id") && (
        <Select
          label="Salesperson"
          allLabel="Everyone"
          value={filters.salesperson_id}
          onChange={(v) => update({ salesperson_id: v })}
          options={reps.map((u) => ({ value: u.id, label: u.full_name }))}
        />
      )}
      {orgWide && !hide.includes("team_id") && (
        <Select label="Team" allLabel="All teams" value={filters.team_id} onChange={(v) => update({ team_id: v })} options={(teams.data ?? []).map((t) => ({ value: t.id, label: t.name }))} />
      )}
      {orgWide && !hide.includes("territory_id") && (
        <Select
          label="Territory"
          allLabel="All territories"
          value={filters.territory_id}
          onChange={(v) => update({ territory_id: v })}
          options={(territories.data ?? []).map((t) => ({ value: t.id, label: t.region ? `${t.name} (${t.region})` : t.name }))}
        />
      )}
      {!hide.includes("customer_segment") && (
        <Select
          label="Segment"
          allLabel="All segments"
          value={filters.customer_segment}
          onChange={(v) => update({ customer_segment: v })}
          options={(segments.data ?? []).map((s) => ({ value: s, label: s }))}
        />
      )}
      {!hide.includes("product_id") && (
        <Select
          label="Product"
          allLabel="All products"
          value={filters.product_id}
          onChange={(v) => update({ product_id: v })}
          options={(products.data?.items ?? []).map((p) => ({ value: p.id, label: p.name }))}
        />
      )}

      {activeDimensions > 0 && (
        <button
          className="btn-ghost py-1.5"
          onClick={() => update({ salesperson_id: "", team_id: "", territory_id: "", customer_segment: "", product_id: "" })}
        >
          Clear {activeDimensions} filter{activeDimensions > 1 ? "s" : ""}
        </button>
      )}
    </div>
  );
}
