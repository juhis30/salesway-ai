"use client";

import { useEffect, useState } from "react";
import { Empty, ErrorBox, Skeleton, useApi } from "@/components/ui";
import type { Page, Product } from "@/lib/api";
import { inr } from "@/lib/format";
import { PAGE_SIZE, Pager, useDebounced } from "@/components/table";

export default function ProductsPage() {
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const debouncedSearch = useDebounced(search);
  useEffect(() => setOffset(0), [debouncedSearch]);

  const products = useApi<Page<Product>>("/products", { search: debouncedSearch, limit: PAGE_SIZE, offset });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Products</h1>
        <p className="mt-1 text-sm text-ink-2">Your product catalogue.</p>
      </div>

      <input className="input max-w-xs" placeholder="Search by name or SKU…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search products" />

      <div className="card overflow-hidden">
        {products.error ? (
          <div className="p-4">
            <ErrorBox message={products.error} onRetry={products.reload} />
          </div>
        ) : !products.data ? (
          <Skeleton className="m-4 h-64" />
        ) : products.data.items.length === 0 ? (
          <Empty title="No products found" hint={search ? "Try a different search." : "Import a product file to get started."} />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px]">
                <thead>
                  <tr>
                    <th className="th">SKU</th>
                    <th className="th">Name</th>
                    <th className="th">Category</th>
                    <th className="th text-right">List price</th>
                  </tr>
                </thead>
                <tbody className={products.loading ? "opacity-60" : ""}>
                  {products.data.items.map((p) => (
                    <tr key={p.id} className="hover:bg-surface-2">
                      <td className="td tabular text-ink-2">{p.sku}</td>
                      <td className="td font-medium">{p.name}</td>
                      <td className="td">{p.category ?? "—"}</td>
                      <td className="td tabular text-right">{inr(p.unit_price)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager total={products.data.total} offset={offset} onChange={setOffset} />
          </>
        )}
      </div>
    </div>
  );
}
