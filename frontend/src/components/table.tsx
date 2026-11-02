"use client";

import { useEffect, useState } from "react";

export const PAGE_SIZE = 25;

export function useDebounced<T>(value: T, ms = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

export function Pager({ total, offset, onChange }: { total: number; offset: number; onChange: (offset: number) => void }) {
  if (total <= PAGE_SIZE) return null;
  return (
    <div className="flex items-center justify-between border-t border-line px-4 py-3 text-sm text-ink-2">
      <span className="tabular">
        {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total.toLocaleString("en-IN")}
      </span>
      <div className="flex gap-2">
        <button className="btn-ghost py-1" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - PAGE_SIZE))}>
          Previous
        </button>
        <button className="btn-ghost py-1" disabled={offset + PAGE_SIZE >= total} onClick={() => onChange(offset + PAGE_SIZE)}>
          Next
        </button>
      </div>
    </div>
  );
}
