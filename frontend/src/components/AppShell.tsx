"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ROLE_LABELS, type Role } from "@/lib/api";
import { canImport, isAdmin, useAuth } from "@/lib/auth";

interface NavItem {
  href: string;
  label: string;
  icon: string; // SVG path
  show?: (role: Role) => boolean;
}

const NAV: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: "M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z" },
  { href: "/insights/customers", label: "Customer health", icon: "M20.8 4.6a5.5 5.5 0 00-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 00-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 000-7.8z" },
  { href: "/insights/products", label: "Product trends", icon: "M3 17l6-6 4 4 8-8M15 7h6v6" },
  { href: "/pipeline", label: "Pipeline", icon: "M3 4h18l-7 8v6l-4 2v-8L3 4z" },
  { href: "/activity", label: "Team activity", icon: "M22 12h-4l-3 9L9 3l-3 9H2" },
  { href: "/customers", label: "Customers", icon: "M16 11a4 4 0 10-8 0 4 4 0 008 0zM4 21a8 8 0 0116 0" },
  { href: "/products", label: "Products", icon: "M21 8l-9-5-9 5 9 5 9-5zM3 8v8l9 5 9-5V8" },
  { href: "/imports", label: "Data import", icon: "M12 3v12m0 0l-4-4m4 4l4-4M4 17v3h16v-3", show: canImport },
  { href: "/users", label: "Users & teams", icon: "M17 20v-2a4 4 0 00-4-4H7a4 4 0 00-4 4v2M10 10a4 4 0 100-8 4 4 0 000 8zM21 20v-2a4 4 0 00-3-3.87M16 2.13a4 4 0 010 7.75", show: (r) => r !== "sales_executive" },
];

type Theme = "system" | "light" | "dark";

function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>("system");
  useEffect(() => {
    try {
      const saved = localStorage.getItem("sw_theme") as Theme | null;
      if (saved) setTheme(saved);
    } catch {
      /* ignore */
    }
  }, []);
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("sw_theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  return [theme, setTheme];
}

function Icon({ path }: { path: string }) {
  return (
    <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={path} />
    </svg>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { me, loading, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const [theme, setTheme] = useTheme();

  useEffect(() => {
    if (!loading && !me) router.replace("/login");
  }, [loading, me, router]);

  useEffect(() => setMenuOpen(false), [pathname]);

  if (loading || !me) {
    return <div className="flex min-h-screen items-center justify-center text-sm text-muted">Loading…</div>;
  }

  const items = NAV.filter((item) => !item.show || item.show(me.user.role));
  const initials = me.user.full_name.split(" ").map((p) => p[0]).slice(0, 2).join("");

  const nav = (
    <nav className="flex flex-col gap-0.5" aria-label="Main">
      {items.map((item) => {
        const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition ${
              active ? "bg-accent-soft font-medium text-ink" : "text-ink-2 hover:bg-surface-2 hover:text-ink"
            }`}
          >
            <Icon path={item.icon} />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <div className="min-h-screen md:flex">
      <aside
        className={`${menuOpen ? "flex" : "hidden"} fixed inset-0 z-30 flex-col border-r border-line bg-surface p-4 md:sticky md:top-0 md:flex md:h-screen md:w-60 md:shrink-0`}
      >
        <div className="mb-6 flex items-center justify-between">
          <Link href="/dashboard" className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-sm font-bold text-accent-ink">S</span>
            <span className="text-base font-semibold">SaleSway AI</span>
          </Link>
          <button className="btn-ghost px-2 py-1 md:hidden" onClick={() => setMenuOpen(false)} aria-label="Close menu">
            ✕
          </button>
        </div>
        {nav}
        <div className="mt-auto space-y-3 border-t border-line pt-4">
          <label className="flex items-center justify-between text-xs text-ink-2">
            Theme
            <select className="rounded-md border border-line bg-surface px-2 py-1 text-xs" value={theme} onChange={(e) => setTheme(e.target.value as Theme)}>
              <option value="system">System</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </label>
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-surface-2 text-xs font-semibold">{initials}</span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{me.user.full_name}</p>
              <p className="truncate text-xs text-muted">{ROLE_LABELS[me.user.role]}</p>
            </div>
          </div>
          <button
            className="btn-ghost w-full"
            onClick={() => {
              logout();
              router.replace("/login");
            }}
          >
            Sign out
          </button>
        </div>
      </aside>

      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-20 flex items-center gap-3 border-b border-line bg-page/90 px-4 py-3 backdrop-blur md:px-8">
          <button className="btn-ghost px-2 py-1 md:hidden" onClick={() => setMenuOpen(true)} aria-label="Open menu">
            ☰
          </button>
          <p className="truncate text-sm text-ink-2">
            <span className="font-medium text-ink">{me.organization.name}</span>
            {isAdmin(me.user.role) && <span className="ml-2 rounded bg-surface-2 px-1.5 py-0.5 text-xs">{me.organization.plan}</span>}
          </p>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-6 md:px-8">{children}</main>
      </div>
    </div>
  );
}
