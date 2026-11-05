"use client";

import { useMemo, useState } from "react";
import { Empty, ErrorBox, SectionCard, Skeleton, useApi } from "@/components/ui";
import { api, PASSWORD_HINT, ROLE_LABELS, type Role, type Team, type Territory, type User } from "@/lib/api";
import { isAdmin, useAuth } from "@/lib/auth";

const ASSIGNABLE_ROLES = (Object.keys(ROLE_LABELS) as Role[]).filter((r) => r !== "super_admin");

export default function UsersPage() {
  const { me } = useAuth();
  const admin = isAdmin(me!.user.role);
  const users = useApi<User[]>("/users");
  const teams = useApi<Team[]>("/teams");
  const territories = useApi<Territory[]>("/territories");

  const teamNames = useMemo(() => new Map((teams.data ?? []).map((t) => [t.id, t.name])), [teams.data]);
  const territoryNames = useMemo(() => new Map((territories.data ?? []).map((t) => [t.id, t.name])), [territories.data]);
  const [actionError, setActionError] = useState<string | null>(null);
  const [resetting, setResetting] = useState<User | null>(null);
  const demo = me!.organization.plan === "demo";

  async function toggleActive(user: User) {
    setActionError(null);
    try {
      await api(`/users/${user.id}`, { method: "PATCH", json: { is_active: !user.is_active } });
      users.reload();
    } catch (err) {
      setActionError((err as Error).message);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Users & teams</h1>
        <p className="mt-1 text-sm text-ink-2">
          {admin ? "Add people, assign them to teams and territories, and control access." : "People whose numbers you can see."}
        </p>
      </div>

      {admin && teams.data && territories.data && (
        <AddUser teams={teams.data} territories={territories.data} onCreated={users.reload} />
      )}

      {resetting && <ResetPassword user={resetting} onClose={() => setResetting(null)} />}

      <SectionCard title="People" subtitle={users.data && `${users.data.length} users`}>
        {actionError && (
          <div className="mb-4">
            <ErrorBox message={actionError} />
          </div>
        )}
        {users.error ? (
          <ErrorBox message={users.error} onRetry={users.reload} />
        ) : !users.data ? (
          <Skeleton className="h-64" />
        ) : users.data.length === 0 ? (
          <Empty title="No users" />
        ) : (
          <div className="-mx-4 overflow-x-auto">
            <table className="w-full min-w-[720px]">
              <thead>
                <tr>
                  <th className="th">Name</th>
                  <th className="th">Role</th>
                  <th className="th">Team</th>
                  <th className="th">Territory</th>
                  <th className="th">Status</th>
                  {admin && <th className="th" />}
                </tr>
              </thead>
              <tbody>
                {users.data.map((u) => (
                  <tr key={u.id} className="hover:bg-surface-2">
                    <td className="td">
                      <p className="font-medium">{u.full_name}</p>
                      <p className="text-xs text-muted">{u.email}</p>
                    </td>
                    <td className="td">{ROLE_LABELS[u.role]}</td>
                    <td className="td">{(u.team_id && teamNames.get(u.team_id)) || "—"}</td>
                    <td className="td">{(u.territory_id && territoryNames.get(u.territory_id)) || "—"}</td>
                    <td className="td">{u.is_active ? "Active" : <span className="text-muted">Deactivated</span>}</td>
                    {admin && (
                      <td className="td text-right">
                        {u.id !== me!.user.id && (
                          <div className="flex justify-end gap-4">
                            {!demo && (
                              <button className="text-sm font-medium text-accent hover:underline" onClick={() => setResetting(u)}>
                                Reset password
                              </button>
                            )}
                            <button className="text-sm font-medium text-accent hover:underline" onClick={() => toggleActive(u)}>
                              {u.is_active ? "Deactivate" : "Reactivate"}
                            </button>
                          </div>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      {admin && (
        <div className="grid gap-6 md:grid-cols-2">
          <NameList title="Teams" singular="team" items={teams.data} path="/teams" onCreated={teams.reload} />
          <NameList title="Territories" singular="territory" items={territories.data} path="/territories" withRegion onCreated={territories.reload} />
        </div>
      )}
    </div>
  );
}

function AddUser({ teams, territories, onCreated }: { teams: Team[]; territories: Territory[]; onCreated: () => void }) {
  const empty = { full_name: "", email: "", password: "", role: "sales_executive" as Role, phone: "", team_id: "", territory_id: "" };
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(empty);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [key]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/users", {
        method: "POST",
        json: { ...form, phone: form.phone || null, team_id: form.team_id || null, territory_id: form.territory_id || null },
      });
      setForm(empty);
      setOpen(false);
      onCreated();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button className="btn-primary" onClick={() => setOpen(true)}>
        Add user
      </button>
    );
  }

  return (
    <SectionCard title="Add user">
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <label className="label" htmlFor="u-name">Full name</label>
          <input id="u-name" className="input" required value={form.full_name} onChange={set("full_name")} />
        </div>
        <div>
          <label className="label" htmlFor="u-email">Email</label>
          <input id="u-email" type="email" className="input" required value={form.email} onChange={set("email")} />
        </div>
        <div>
          <label className="label" htmlFor="u-pass">Temporary password</label>
          <input id="u-pass" type="password" className="input" required minLength={10} maxLength={72} title={PASSWORD_HINT} value={form.password} onChange={set("password")} />
        </div>
        <div>
          <label className="label" htmlFor="u-phone">WhatsApp number (optional)</label>
          <input id="u-phone" className="input" placeholder="+919876543210" pattern="^\+[1-9]\d{7,14}$" value={form.phone} onChange={set("phone")} />
        </div>
        <div>
          <label className="label" htmlFor="u-role">Role</label>
          <select id="u-role" className="input" value={form.role} onChange={set("role")}>
            {ASSIGNABLE_ROLES.map((r) => (
              <option key={r} value={r}>{ROLE_LABELS[r]}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="u-team">Team</label>
          <select id="u-team" className="input" value={form.team_id} onChange={set("team_id")}>
            <option value="">No team</option>
            {teams.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="u-territory">Territory</label>
          <select id="u-territory" className="input" value={form.territory_id} onChange={set("territory_id")}>
            <option value="">No territory</option>
            {territories.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        </div>
        <div className="flex items-end gap-2">
          <button className="btn-primary" disabled={busy}>{busy ? "Adding…" : "Add"}</button>
          <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>Cancel</button>
        </div>
        {error && <p role="alert" className="text-sm text-critical sm:col-span-2 lg:col-span-4">{error}</p>}
      </form>
    </SectionCard>
  );
}

// Sets a temporary password to pass on to the person. It also unlocks the account and signs them out everywhere.
function ResetPassword({ user, onClose }: { user: User; onClose: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(`/users/${user.id}/reset-password`, { method: "POST", json: { new_password: password } });
      setDone(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <SectionCard title={`Reset password for ${user.full_name}`} subtitle="Unlocks the account and signs them out on every device.">
      {done ? (
        <div className="flex items-center justify-between gap-4">
          <p role="status" className="text-sm text-good">Done. Give {user.full_name} the temporary password and ask them to change it from their Account page.</p>
          <button className="btn-ghost" onClick={onClose}>Close</button>
        </div>
      ) : (
        <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
          <div className="min-w-64 flex-1">
            <label className="label" htmlFor="reset-pass">Temporary password</label>
            <input id="reset-pass" type="password" className="input" autoComplete="new-password" required minLength={10} maxLength={72}
              aria-describedby="reset-hint" value={password} onChange={(e) => setPassword(e.target.value)} />
            <p id="reset-hint" className="mt-1 text-xs text-muted">{PASSWORD_HINT}</p>
          </div>
          <div className="flex gap-2 pb-5">
            <button className="btn-primary" disabled={busy}>{busy ? "Resetting…" : "Reset password"}</button>
            <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
          </div>
          {error && <p role="alert" className="w-full text-sm text-critical">{error}</p>}
        </form>
      )}
    </SectionCard>
  );
}

function NameList({
  title,
  items,
  path,
  withRegion = false,
  singular,
  onCreated,
}: {
  title: string;
  items: (Team | Territory)[] | null;
  path: string;
  withRegion?: boolean;
  singular: string;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [region, setRegion] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api(path, { method: "POST", json: withRegion ? { name, region: region || null } : { name } });
      setName("");
      setRegion("");
      onCreated();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <SectionCard title={title}>
      <ul className="mb-4 divide-y divide-line">
        {(items ?? []).map((item) => (
          <li key={item.id} className="flex justify-between py-2 text-sm">
            {item.name}
            {"region" in item && item.region && <span className="text-muted">{item.region}</span>}
          </li>
        ))}
      </ul>
      <form onSubmit={add} className="flex gap-2">
        <input className="input" placeholder={`New ${singular}`} required value={name} onChange={(e) => setName(e.target.value)} />
        {withRegion && <input className="input w-32" placeholder="Region" value={region} onChange={(e) => setRegion(e.target.value)} />}
        <button className="btn-ghost">Add</button>
      </form>
      {error && <p role="alert" className="mt-2 text-sm text-critical">{error}</p>}
    </SectionCard>
  );
}
