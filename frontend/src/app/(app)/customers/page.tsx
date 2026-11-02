"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { PAGE_SIZE, Pager, useDebounced } from "@/components/table";
import { Empty, ErrorBox, Skeleton, useApi } from "@/components/ui";
import type { Customer, Page, Territory, User } from "@/lib/api";

export default function CustomersPage() {
  const [search, setSearch] = useState("");
  const [segment, setSegment] = useState("");
  const [offset, setOffset] = useState(0);
  const debouncedSearch = useDebounced(search);

  useEffect(() => {
    setOffset(0);
  }, [debouncedSearch, segment]);

  const customers = useApi<Page<Customer>>("/customers", { search: debouncedSearch, segment, limit: PAGE_SIZE, offset });
  const segments = useApi<string[]>("/customers/segments");
  const users = useApi<User[]>("/users");
  const territories = useApi<Territory[]>("/territories");

  const userNames = useMemo(() => new Map((users.data ?? []).map((u) => [u.id, u.full_name])), [users.data]);
  const territoryNames = useMemo(() => new Map((territories.data ?? []).map((t) => [t.id, t.name])), [territories.data]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Customers</h1>
        <p className="mt-1 text-sm text-ink-2">The customers you can see, based on your role.</p>
      </div>

      <div className="flex flex-wrap gap-3">
        <input
          className="input max-w-xs"
          placeholder="Search by name or code…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search customers"
        />
        <select className="input w-auto" value={segment} onChange={(e) => setSegment(e.target.value)} aria-label="Segment">
          <option value="">All segments</option>
          {(segments.data ?? []).map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </div>

      <div className="card overflow-hidden">
        {customers.error ? (
          <div className="p-4">
            <ErrorBox message={customers.error} onRetry={customers.reload} />
          </div>
        ) : !customers.data ? (
          <Skeleton className="m-4 h-64" />
        ) : customers.data.items.length === 0 ? (
          <Empty title="No customers found" hint={search || segment ? "Try a different search." : "Import a customer file to get started."} />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px]">
                <thead>
                  <tr>
                    <th className="th">Code</th>
                    <th className="th">Name</th>
                    <th className="th">Segment</th>
                    <th className="th">City</th>
                    <th className="th">Territory</th>
                    <th className="th">Salesperson</th>
                  </tr>
                </thead>
                <tbody className={customers.loading ? "opacity-60" : ""}>
                  {customers.data.items.map((c) => (
                    <tr key={c.id} className="hover:bg-surface-2">
                      <td className="td tabular text-ink-2">{c.code ?? "—"}</td>
                      <td className="td font-medium">
                        <Link href={`/customers/${c.id}`} className="hover:text-accent hover:underline">
                          {c.name}
                        </Link>
                      </td>
                      <td className="td">{c.segment ?? "—"}</td>
                      <td className="td">{c.city ?? "—"}</td>
                      <td className="td">{(c.territory_id && territoryNames.get(c.territory_id)) || "—"}</td>
                      <td className="td">{(c.owner_id && userNames.get(c.owner_id)) || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager total={customers.data.total} offset={offset} onChange={setOffset} />
          </>
        )}
      </div>
    </div>
  );
}
