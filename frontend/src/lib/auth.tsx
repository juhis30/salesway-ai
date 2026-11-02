"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, clearTokens, hasSession, LOGOUT_EVENT, setTokens, type Me, type Role } from "./api";

interface RegisterInput {
  organization_name: string;
  full_name: string;
  email: string;
  password: string;
  industry?: string;
}

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (input: RegisterInput) => Promise<void>;
  logout: () => void;
}

type Tokens = { access_token: string; refresh_token: string };

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const loadMe = useCallback(async () => {
    setMe(await api<Me>("/auth/me"));
  }, []);

  useEffect(() => {
    if (!hasSession()) {
      setLoading(false);
      return;
    }
    loadMe()
      .catch(() => clearTokens())
      .finally(() => setLoading(false));
  }, [loadMe]);

  const logout = useCallback(() => {
    clearTokens();
    setMe(null);
  }, []);

  useEffect(() => {
    window.addEventListener(LOGOUT_EVENT, logout);
    return () => window.removeEventListener(LOGOUT_EVENT, logout);
  }, [logout]);

  const login = useCallback(
    async (email: string, password: string) => {
      setTokens(await api<Tokens>("/auth/login", { method: "POST", json: { email, password } }));
      await loadMe();
    },
    [loadMe],
  );

  const register = useCallback(
    async (input: RegisterInput) => {
      setTokens(await api<Tokens>("/auth/register", { method: "POST", json: input }));
      await loadMe();
    },
    [loadMe],
  );

  const value = useMemo(() => ({ me, loading, login, register, logout }), [me, loading, login, register, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

// UI-side role checks mirror the backend. They only hide controls; the API enforces access.
export const isAdmin = (role: Role) => role === "org_admin" || role === "super_admin";
export const canImport = (role: Role) => isAdmin(role) || role === "analyst";
export const isOrgWide = (role: Role) => isAdmin(role) || role === "sales_head" || role === "analyst";
