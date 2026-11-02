"use client";

import Link from "next/link";
import { useState } from "react";
import { ForecastChart } from "@/components/ForecastChart";
import { Badge, Empty, ErrorBox, SectionCard, Skeleton, StatTile, useApi, type Status } from "@/components/ui";
import {
  api,
  type ActionKind,
  type MLModelInfo,
  type MonthEndForecast,
  type NextAction,
  type NextMonthForecast,
  type Scores,
  type User,
} from "@/lib/api";
import { canImport, isOrgWide, useAuth } from "@/lib/auth";
import { fmtDate, fmtLongMonth, inr, inrCompact, pct } from "@/lib/format";

const KIND: Record<ActionKind, { label: string; value: string }> = {
  follow_up: { label: "Promised follow-up", value: "customer worth" },
  win_back: { label: "Win back", value: "at stake" },
  revive_deal: { label: "Stalled deal", value: "weighted value" },
  cross_sell: { label: "Cross-sell", value: "potential" },
};

function chanceStatus(p: number | null): { status: Status; label: string } | null {
  if (p === null) return null;
  if (p >= 0.7) return { status: "good", label: "Likely" };
  if (p >= 0.4) return { status: "warning", label: "Uncertain" };
  return { status: "critical", label: "Unlikely" };
}

const MODEL_STATUS: Record<MLModelInfo["status"], { status: Status; label: string }> = {
  active: { status: "good", label: "In use" },
  rejected: { status: "warning", label: "Not used: didn't beat the simple method" },
  insufficient_data: { status: "warning", label: "Not enough data yet" },
  retired: { status: "warning", label: "Replaced" },
};

export default function ForecastPage() {
  const { me } = useAuth();
  const role = me!.user.role;
  const [salespersonId, setSalespersonId] = useState("");
  const users = useApi<User[]>("/users");
  const reps = (users.data ?? []).filter((u) => u.role === "sales_executive");

  const forecast = useApi<MonthEndForecast>("/predictions/month-end", { salesperson_id: salespersonId });
  const nextMonth = useApi<NextMonthForecast>(isOrgWide(role) ? "/predictions/next-month" : null);
  const models = useApi<MLModelInfo[]>("/ml/models");
  const churn = useApi<Scores>("/predictions/churn");
  const deals = useApi<Scores>("/predictions/deals");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Forecast &amp; next actions</h1>
          <p className="mt-1 max-w-3xl text-sm text-ink-2">
            Where this month is heading, and what to do about it. Every range and percentage comes from how the method performed on your own past months.
          </p>
        </div>
        {reps.length > 1 && (
          <label className="flex flex-col">
            <span className="label">Forecast for</span>
            <select className="input py-1.5" value={salespersonId} onChange={(e) => setSalespersonId(e.target.value)}>
              <option value="">{isOrgWide(role) ? "Whole company" : "Everyone I can see"}</option>
              {reps.map((u) => (
                <option key={u.id} value={u.id}>{u.full_name}</option>
              ))}
            </select>
          </label>
        )}
      </div>

      {forecast.error ? (
        <ErrorBox message={forecast.error} onRetry={forecast.reload} />
      ) : !forecast.data ? (
        <Skeleton className="h-96" />
      ) : !forecast.data.available ? (
        <div className="card">
          <Empty title="No forecast yet" hint={forecast.data.reason ?? undefined} />
        </div>
      ) : (
        <MonthEnd data={forecast.data} loading={forecast.loading} />
      )}

      {nextMonth.data?.available && (
        <SectionCard
          title={`Next month: ${fmtLongMonth(nextMonth.data.month!)}`}
          subtitle={`Tested on only ${nextMonth.data.based_on_samples} past months (average error ${pct(nextMonth.data.tested_error_pct)}): treat as a rough guide.`}
        >
          <p className="text-2xl font-semibold" title={inr(nextMonth.data.forecast)}>{inrCompact(nextMonth.data.forecast)}</p>
          <p className="text-sm text-ink-2">Past outcomes ranged {inrCompact(nextMonth.data.lower)} – {inrCompact(nextMonth.data.upper)}</p>
        </SectionCard>
      )}

      <Actions />

      <div className="grid gap-6 lg:grid-cols-2">
        <ScoreCard
          title="Customers likely to stop ordering"
          data={churn.data}
          valueLabel="monthly value"
          fallback={<>The rule-based list of customers at risk is on <Link href="/insights/customers" className="text-accent hover:underline">Customer health</Link>.</>}
        />
        <ScoreCard
          title="Open deals most likely to win"
          data={deals.data}
          valueLabel="deal value"
          fallback={<>Open deals and stalled ones are on the <Link href="/pipeline" className="text-accent hover:underline">Pipeline</Link> page.</>}
        />
      </div>

      <Models models={models.data} canTrain={canImport(role)} onTrained={() => {
        models.reload(); forecast.reload(); churn.reload(); deals.reload(); nextMonth.reload();
      }} />
    </div>
  );
}

function MonthEnd({ data, loading }: { data: MonthEndForecast; loading: boolean }) {
  const chance = chanceStatus(data.chance_of_target);
  return (
    <div className={`space-y-4 ${loading ? "opacity-60" : ""}`}>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="sm:col-span-2">
          <StatTile
            hero
            label={`Expected total for ${fmtLongMonth(data.month!)}`}
            value={inrCompact(data.forecast)}
            title={inr(data.forecast)}
            footer={<span className="text-xs text-ink-2">Likely between {inrCompact(data.lower)} and {inrCompact(data.upper)}</span>}
          />
        </div>
        <StatTile
          label="Chance of reaching target"
          value={data.chance_of_target === null ? "—" : pct(data.chance_of_target * 100, 0)}
          footer={chance ? <Badge status={chance.status} label={`${chance.label} · target ${inrCompact(data.target)}`} /> : <span className="text-xs text-muted">No target set</span>}
        />
        <StatTile label="Sales so far" value={inrCompact(data.so_far)} title={inr(data.so_far)} footer={<span className="text-xs text-muted">up to {fmtDate(data.as_of)}</span>} />
      </div>
      <SectionCard
        title="This month so far, and where it's heading"
        subtitle={`Method: ${data.method_label}. Tested on ${data.model?.metrics?.months_tested ?? "past"} months it was off by ${pct(data.tested_error_pct)} on average (the simple pace rule: ${pct(data.pace_error_pct)}).${
          data.level === "individual" ? " One person's sales vary more than a company's, so the range is wider." : ""
        }`}
      >
        <ForecastChart data={data} />
      </SectionCard>
    </div>
  );
}

const TEAM_PER_PERSON = 2;

function Actions() {
  const { me } = useAuth();
  const actions = useApi<NextAction[]>("/recommendations");
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"mine" | "team" | null>(null);

  const all = actions.data ?? [];
  const mine = all.filter((a) => a.owner_id === me!.user.id);
  // The team view shows each person's top actions (the API returns them in priority order).
  const perPerson = new Map<string, number>();
  const team = all.filter((a) => {
    if (a.owner_id === me!.user.id) return false;
    const seen = perPerson.get(a.owner_id) ?? 0;
    perPerson.set(a.owner_id, seen + 1);
    return seen < TEAM_PER_PERSON;
  });
  const hasTeam = all.some((a) => a.owner_id !== me!.user.id);
  const current = tab ?? (mine.length > 0 || !hasTeam ? "mine" : "team");
  const shown = current === "mine" ? mine : team;

  async function update(id: string, status: "done" | "dismissed") {
    setError(null);
    try {
      await api(`/recommendations/${id}`, { method: "PATCH", json: { status } });
      actions.reload();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <SectionCard
      title="Next best actions"
      subtitle="Ordered: promises already made, then customers drifting away, then stalled deals, then new sales. Done or dismissed actions aren't suggested again for 2 weeks."
      action={
        hasTeam && (
          <div className="flex gap-1" role="tablist">
            {(
              [
                ["mine", `Mine (${mine.length})`],
                ["team", `Team: top ${TEAM_PER_PERSON} each (${team.length})`],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                role="tab"
                aria-selected={current === value}
                onClick={() => setTab(value)}
                className={`rounded-md px-3 py-1 text-sm ${current === value ? "bg-accent-soft font-medium text-ink" : "text-ink-2 hover:bg-surface-2"}`}
              >
                {label}
              </button>
            ))}
          </div>
        )
      }
    >
      {error && <div className="mb-3"><ErrorBox message={error} /></div>}
      {actions.error ? (
        <ErrorBox message={actions.error} onRetry={actions.reload} />
      ) : !actions.data ? (
        <Skeleton className="h-48" />
      ) : shown.length === 0 ? (
        <Empty
          title={current === "mine" && hasTeam ? "No actions for you personally" : "Nothing to suggest right now"}
          hint={current === "mine" && hasTeam ? "Your team's actions are on the Team tab." : "Suggestions refresh each time the models are retrained."}
        />
      ) : (
        <ul className="-mx-4 -mb-4">
          {shown.map((a) => (
            <li key={a.id} className="flex flex-wrap items-start gap-x-4 gap-y-2 border-t border-line px-4 py-3">
              <span className="w-32 shrink-0 text-xs font-medium uppercase tracking-wide text-muted">{KIND[a.kind].label}</span>
              <div className="min-w-0 flex-1">
                <p className="font-medium">{a.title}</p>
                <p className="text-sm text-ink-2">{a.reason}</p>
                {a.owner_id !== me!.user.id && a.owner_name && <p className="mt-0.5 text-xs text-muted">For {a.owner_name}</p>}
              </div>
              {a.expected_value && Number(a.expected_value) > 0 && (
                <span className="w-28 text-right text-sm">
                  <span className="tabular font-medium">{inrCompact(a.expected_value)}</span>
                  <span className="block text-xs text-muted">{KIND[a.kind].value}</span>
                </span>
              )}
              <div className="flex gap-2">
                <button className="btn-ghost py-1" onClick={() => update(a.id, "done")}>Done</button>
                <button className="py-1 text-sm text-muted hover:text-ink" onClick={() => update(a.id, "dismissed")}>Dismiss</button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}

function ScoreCard({ title, data, valueLabel, fallback }: { title: string; data: Scores | null; valueLabel: string; fallback: React.ReactNode }) {
  return (
    <SectionCard title={title}>
      {!data ? (
        <Skeleton className="h-32" />
      ) : !data.available ? (
        <div className="text-sm text-ink-2">
          <Badge status="warning" label="Model not in use" />
          <p className="mt-2">{data.reason}</p>
          <p className="mt-2">{fallback}</p>
        </div>
      ) : data.rows.length === 0 ? (
        <Empty title="Nothing in your scope" />
      ) : (
        <ul className="divide-y divide-line">
          {data.rows.slice(0, 8).map((r) => (
            <li key={r.entity_id} className="flex items-start justify-between gap-3 py-2 text-sm">
              <div className="min-w-0">
                <p className="font-medium">{r.name}</p>
                <p className="text-xs text-muted">{[r.owner_name, ...r.signals].filter(Boolean).join(" · ")}</p>
              </div>
              <div className="shrink-0 text-right">
                <p className="tabular font-medium">{pct(r.probability * 100, 0)}</p>
                <p className="text-xs text-muted">{inrCompact(r.value)} {valueLabel}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}

function describe(m: MLModelInfo): string {
  const metrics = m.metrics as Record<string, unknown>;
  if (m.kind === "month_end_forecast" || m.kind === "next_month_forecast") {
    return `Average error when tested: ${pct(metrics.chosen_mape_pct as number)}${
      metrics.baseline_mape_pct !== undefined ? ` (simple pace rule: ${pct(metrics.baseline_mape_pct as number)})` : ""
    }`;
  }
  const evaluation = metrics.evaluation as Record<string, { auc: number }> | undefined;
  if (!evaluation) return "";
  return "Ranking accuracy on unseen examples (AUC, 0.5 = coin toss, 1 = perfect): " +
    Object.entries(evaluation).map(([name, e]) => `${name.replace(/_/g, " ")} ${e.auc}`).join(" · ");
}

function Models({ models, canTrain, onTrained }: { models: MLModelInfo[] | null; canTrain: boolean; onTrained: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function retrain() {
    setBusy(true);
    setError(null);
    try {
      await api("/ml/train", { method: "POST" });
      onTrained();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <SectionCard
      title="How the predictions are made"
      subtitle="Each model is tested on past data it didn't learn from, and is only used if it beats the simpler method already in place."
      action={canTrain && <button className="btn-ghost" disabled={busy} onClick={retrain}>{busy ? "Retraining…" : "Retrain now"}</button>}
    >
      {error && <div className="mb-3"><ErrorBox message={error} /></div>}
      {!models ? (
        <Skeleton className="h-40" />
      ) : models.length === 0 ? (
        <Empty title="No models trained yet" hint={canTrain ? "Select “Retrain now”." : "Ask an admin to train the models."} />
      ) : (
        <ul className="-mx-4 -mb-4">
          {models.map((m) => (
            <li key={m.id} className="border-t border-line px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-medium">{m.label} <span className="text-xs font-normal text-muted">v{m.version} · trained {fmtDate(m.trained_at)}</span></p>
                <Badge {...MODEL_STATUS[m.status]} />
              </div>
              <p className="mt-1 text-sm text-ink-2">{describe(m)}</p>
              {m.notes && <p className="mt-0.5 text-xs text-muted">{m.notes}</p>}
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}
