"use client";

import { useRef, useState } from "react";
import { Empty, ErrorBox, SectionCard, Skeleton, StatusIcon, useApi } from "@/components/ui";
import { api, type ImportEntity, type ImportField, type ImportJob, type ImportPreview } from "@/lib/api";
import { canImport, useAuth } from "@/lib/auth";
import { fmtDate } from "@/lib/format";

const ENTITIES: { value: ImportEntity; label: string; hint: string }[] = [
  { value: "customers", label: "Customers", hint: "Import first. Assign each customer to a salesperson by email." },
  { value: "products", label: "Products", hint: "Your catalogue, keyed by SKU." },
  { value: "orders", label: "Orders", hint: "One row per order line. Refers to customers, products and salespeople." },
  { value: "targets", label: "Targets", hint: "Monthly revenue target per salesperson (month as YYYY-MM)." },
  { value: "opportunities", label: "Deals", hint: "Open, won and lost opportunities. CRM stage names are recognised." },
  { value: "activities", label: "Activities", hint: "Visits, calls, meetings and planned follow-ups." },
];

function downloadTemplate(entity: ImportEntity, fields: ImportField[]) {
  const blob = new Blob([fields.map((f) => f.name).join(",") + "\n"], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), { href: url, download: `${entity}-template.csv` });
  a.click();
  URL.revokeObjectURL(url);
}

export default function ImportsPage() {
  const { me } = useAuth();
  if (!canImport(me!.user.role)) {
    return <Empty title="Data import is for admins and analysts" hint="Ask your organisation admin to upload data." />;
  }
  return <Importer />;
}

function Importer() {
  const [entity, setEntity] = useState<ImportEntity>("customers");
  const [file, setFile] = useState<File | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [done, setDone] = useState<ImportJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const fields = useApi<ImportField[]>(`/imports/fields/${entity}`);
  const history = useApi<ImportJob[]>("/imports");

  function reset(nextEntity = entity) {
    setEntity(nextEntity);
    setFile(null);
    setMapping({});
    setPreview(null);
    setDone(null);
    setError(null);
    if (fileInput.current) fileInput.current.value = "";
  }

  async function runPreview(withMapping?: Record<string, string>) {
    if (!file) return;
    setBusy(true);
    setError(null);
    setDone(null);
    const form = new FormData();
    form.append("file", file);
    if (withMapping) form.append("mapping", JSON.stringify(withMapping));
    try {
      const result = await api<ImportPreview>(`/imports/${entity}/preview`, { method: "POST", form });
      setPreview(result);
      setMapping(result.job.column_mapping);
      history.reload();
    } catch (err) {
      setError((err as Error).message);
      setPreview(null);
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      setDone(await api<ImportJob>(`/imports/${preview.job.id}/confirm`, { method: "POST" }));
      setPreview(null);
      history.reload();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const columns = preview ? [...new Set([...Object.values(preview.job.column_mapping), ...preview.unmapped_columns])] : [];
  const mappingChanged = preview && JSON.stringify(mapping) !== JSON.stringify(preview.job.column_mapping);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Data import</h1>
        <p className="mt-1 text-sm text-ink-2">
          Upload CSV or Excel (.xlsx) files. You&apos;ll see a preview with any problems before anything is saved.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" role="radiogroup" aria-label="What are you importing?">
        {ENTITIES.map((e, i) => (
          <button
            key={e.value}
            role="radio"
            aria-checked={entity === e.value}
            onClick={() => reset(e.value)}
            className={`card p-4 text-left transition ${entity === e.value ? "ring-2 ring-accent" : "hover:bg-surface-2"}`}
          >
            <span className="text-xs text-muted">Step {i + 1}</span>
            <p className="font-medium">{e.label}</p>
            <p className="mt-1 text-xs text-ink-2">{e.hint}</p>
          </button>
        ))}
      </div>

      <SectionCard
        title={`Upload ${entity}`}
        action={
          fields.data && (
            <button className="text-sm font-medium text-accent hover:underline" onClick={() => downloadTemplate(entity, fields.data!)}>
              Download template
            </button>
          )
        }
      >
        {fields.data && (
          <p className="mb-4 text-sm text-ink-2">
            Columns:{" "}
            {fields.data.map((f, i) => (
              <span key={f.name}>
                <code className={`rounded bg-surface-2 px-1 text-xs ${f.required ? "font-semibold text-ink" : ""}`}>{f.name}</code>
                {f.required && <span className="text-xs text-muted"> (required)</span>}
                {i < fields.data!.length - 1 && ", "}
              </span>
            ))}
            . Common alternatives such as &ldquo;Qty&rdquo;, &ldquo;Rate&rdquo; or &ldquo;Invoice No&rdquo; are recognised automatically.
          </p>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <input
            ref={fileInput}
            type="file"
            accept=".csv,.xlsx"
            className="text-sm file:mr-3 file:rounded-lg file:border file:border-line file:bg-surface file:px-3 file:py-2 file:text-sm file:text-ink hover:file:bg-surface-2"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setPreview(null);
              setDone(null);
              setError(null);
            }}
          />
          <button className="btn-primary" disabled={!file || busy} onClick={() => runPreview()}>
            {busy && !preview ? "Checking…" : "Check file"}
          </button>
        </div>

        {error && (
          <div className="mt-4">
            <ErrorBox message={error} />
          </div>
        )}

        {done && (
          <div role="status" className="mt-4 flex items-start gap-3 rounded-lg border border-good/40 bg-good/5 p-4 text-sm">
            <StatusIcon status="good" />
            <div>
              <p className="font-medium">Imported {done.filename}</p>
              <p className="mt-0.5 text-ink-2">
                {Object.entries(done.result ?? {})
                  .map(([k, v]) => `${v.toLocaleString("en-IN")} ${k.replace(/_/g, " ")}`)
                  .join(" · ")}
                {done.error_rows > 0 && ` · ${done.error_rows} rows skipped because of errors`}
              </p>
            </div>
          </div>
        )}
      </SectionCard>

      {preview && (
        <SectionCard title="Preview" subtitle={`${preview.job.filename}. Nothing has been saved yet.`}>
          <div className="grid gap-4 sm:grid-cols-3">
            <Count label="Rows in file" value={preview.job.total_rows} />
            <Count label="Ready to import" value={preview.job.valid_rows} status="good" />
            <Count label="Rows with errors" value={preview.job.error_rows} status={preview.job.error_rows ? "critical" : undefined} />
          </div>

          {preview.job.warnings.length > 0 && (
            <ul className="mt-4 space-y-1">
              {preview.job.warnings.map((w) => (
                <li key={w} className="flex items-center gap-2 text-sm text-ink-2">
                  <StatusIcon status="warning" /> {w}
                </li>
              ))}
            </ul>
          )}

          <h3 className="mt-6 text-sm font-semibold">Column mapping</h3>
          <p className="text-xs text-ink-2">Check that each field points to the right column in your file.</p>
          <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {(fields.data ?? []).map((f) => (
              <label key={f.name} className="flex items-center gap-2 text-sm">
                <code className="w-36 shrink-0 truncate text-xs">{f.name}{f.required && " *"}</code>
                <select
                  className="input py-1"
                  value={mapping[f.name] ?? ""}
                  onChange={(e) => {
                    const next = { ...mapping };
                    if (e.target.value) next[f.name] = e.target.value;
                    else delete next[f.name];
                    setMapping(next);
                  }}
                >
                  <option value="">— not in file —</option>
                  {columns.map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          {mappingChanged && (
            <button className="btn-ghost mt-3" disabled={busy} onClick={() => runPreview(mapping)}>
              Re-check with this mapping
            </button>
          )}

          {preview.errors.length > 0 && (
            <>
              <h3 className="mt-6 text-sm font-semibold">Problems found</h3>
              <p className="text-xs text-ink-2">
                These rows will be skipped. Fix them in your file and upload again, or import the valid rows now.
                {preview.job.error_rows > preview.errors.length && ` Showing the first ${preview.errors.length}.`}
              </p>
              <div className="mt-2 max-h-64 overflow-auto rounded-lg border border-line">
                <table className="w-full">
                  <thead className="sticky top-0 bg-surface">
                    <tr>
                      <th className="th w-16">Row</th>
                      <th className="th w-40">Field</th>
                      <th className="th">Problem</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.errors.map((e, i) => (
                      <tr key={i}>
                        <td className="td tabular">{e.row}</td>
                        <td className="td"><code className="text-xs">{e.field ?? "—"}</code></td>
                        <td className="td text-ink-2">{e.message}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {preview.sample.length > 0 && (
            <>
              <h3 className="mt-6 text-sm font-semibold">First rows as they will be imported</h3>
              <div className="mt-2 overflow-x-auto rounded-lg border border-line">
                <table className="w-full whitespace-nowrap">
                  <thead>
                    <tr>
                      {Object.keys(preview.sample[0]).map((k) => (
                        <th key={k} className="th">{k}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.sample.map((row) => (
                      <tr key={String(row.row)}>
                        {Object.entries(row).map(([k, v]) => (
                          <td key={k} className="td tabular">{v ?? <span className="text-muted">—</span>}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <div className="mt-6 flex flex-wrap gap-3">
            <button className="btn-primary" disabled={busy || preview.job.valid_rows === 0 || !!mappingChanged} onClick={confirm}>
              {busy ? "Importing…" : `Import ${preview.job.valid_rows.toLocaleString("en-IN")} rows`}
            </button>
            <button className="btn-ghost" disabled={busy} onClick={() => reset()}>
              Cancel
            </button>
          </div>
        </SectionCard>
      )}

      <SectionCard title="Import history">
        {history.error ? (
          <ErrorBox message={history.error} onRetry={history.reload} />
        ) : !history.data ? (
          <Skeleton className="h-32" />
        ) : history.data.length === 0 ? (
          <Empty title="No imports yet" />
        ) : (
          <div className="-mx-4 overflow-x-auto">
            <table className="w-full min-w-[640px]">
              <thead>
                <tr>
                  <th className="th">Date</th>
                  <th className="th">Type</th>
                  <th className="th">File</th>
                  <th className="th">Status</th>
                  <th className="th text-right">Rows</th>
                  <th className="th text-right">Errors</th>
                </tr>
              </thead>
              <tbody className="tabular">
                {history.data.map((j) => (
                  <tr key={j.id}>
                    <td className="td">{fmtDate(j.created_at)}</td>
                    <td className="td capitalize">{j.entity}</td>
                    <td className="td max-w-[16rem] truncate">{j.filename}</td>
                    <td className="td">
                      <span className="inline-flex items-center gap-1.5">
                        <StatusIcon status={j.status === "completed" ? "good" : j.status === "failed" ? "critical" : "warning"} />
                        {j.status === "previewed" ? "Not confirmed" : j.status === "completed" ? "Imported" : "Failed"}
                      </span>
                    </td>
                    <td className="td text-right">{j.valid_rows.toLocaleString("en-IN")}</td>
                    <td className="td text-right">{j.error_rows.toLocaleString("en-IN")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>
    </div>
  );
}

function Count({ label, value, status }: { label: string; value: number; status?: "good" | "critical" }) {
  return (
    <div className="rounded-lg bg-surface-2 p-3">
      <p className="flex items-center gap-1.5 text-xs text-ink-2">
        {status && value > 0 && <StatusIcon status={status} />}
        {label}
      </p>
      <p className="tabular mt-1 text-2xl font-semibold">{value.toLocaleString("en-IN")}</p>
    </div>
  );
}
