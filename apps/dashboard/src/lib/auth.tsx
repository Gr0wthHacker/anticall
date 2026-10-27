"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, getToken, onUnauthorized, request, setToken } from "./api/client";
import type { Schemas } from "./api/types";

type Me = Schemas["MeResponse"];
type Status = "loading" | "anon" | "authed";

export interface BootstrapInput {
  tenant_name: string;
  admin_email: string;
  admin_password: string;
  admin_display_name?: string;
}

interface AuthValue {
  status: Status;
  me: Me | null;
  login: (email: string, password: string) => Promise<void>;
  bootstrap: (input: BootstrapInput) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<Me | null>(null);

  const logout = useCallback(() => {
    setToken(null);
    setMe(null);
    setStatus("anon");
  }, []);

  const loadMe = useCallback(async () => {
    const profile = await api.get<Me>("/v1/auth/me");
    setMe(profile);
    setStatus("authed");
  }, []);

  useEffect(() => {
    onUnauthorized(logout);
    if (!getToken()) {
      setStatus("anon");
      return () => onUnauthorized(null);
    }
    loadMe().catch(logout);
    return () => onUnauthorized(null);
  }, [loadMe, logout]);

  const login = useCallback(
    async (email: string, password: string) => {
      const token = await request<Schemas["TokenResponse"]>("POST", "/v1/auth/login", {
        body: { email, password },
        auth: false,
      });
      setToken(token.access_token);
      await loadMe();
    },
    [loadMe],
  );

  const bootstrap = useCallback(
    async (input: BootstrapInput) => {
      const res = await request<Schemas["BootstrapResponse"]>("POST", "/v1/tenants", {
        body: input,
        auth: false,
      });
      setToken(res.token.access_token);
      await loadMe();
    },
    [loadMe],
  );

  const value = useMemo(
    () => ({ status, me, login, bootstrap, logout }),
    [status, me, login, bootstrap, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
