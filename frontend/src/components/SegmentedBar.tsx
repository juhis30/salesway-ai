import type { Status } from "./ui";
import { StatusIcon } from "./ui";

const FILL: Record<Status, string> = { good: "var(--good)", warning: "var(--warning)", critical: "var(--critical)" };
// Text inside a fill: white, except near-black on the light warning yellow.
const INK: Record<Status, string> = { good: "#ffffff", warning: "#0b0b0b", critical: "#ffffff" };

/** Tall segmented bar: one whole split into status parts, with a legend in block order. */
export function SegmentedBar({ parts }: { parts: { label: string; value: number; status: Status }[] }) {
  const total = parts.reduce((sum, p) => sum + p.value, 0);
  if (total === 0) return null;
  const description = parts.map((p) => `${p.label}: ${p.value} (${Math.round((p.value / total) * 100)}%)`).join(", ");
  return (
    <div>
      <div className="flex h-7 gap-0.5" role="img" aria-label={description}>
        {parts
          .filter((p) => p.value > 0)
          .map((p) => {
            const share = (p.value / total) * 100;
            return (
              <div
                key={p.label}
                className="flex items-center rounded px-2 text-xs font-medium"
                style={{ width: `${share}%`, background: FILL[p.status], color: INK[p.status] }}
                title={`${p.label}: ${p.value} (${share.toFixed(0)}%)`}
              >
                {share >= 8 && `${share.toFixed(0)}%`}
              </div>
            );
          })}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
        {parts.map((p) => (
          <li key={p.label} className="inline-flex items-center gap-1.5 text-sm text-ink-2">
            <StatusIcon status={p.status} />
            {p.label}
          </li>
        ))}
      </ul>
    </div>
  );
}
