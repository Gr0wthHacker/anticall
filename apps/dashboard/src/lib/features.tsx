"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";
import { api } from "./api/client";
import type { CallbackCapabilities, Schemas } from "./api/types";
import { useResource } from "./hooks";

interface FeaturesValue {
  loaded: boolean;
  error: unknown;
  /** Gated-feature flags from /v1/meta/capabilities. Missing flag means OFF. */
  flags: Record<string, boolean>;
  version: string | null;
  /** Callback feature flag and ceilings from /v1/callback/capabilities. */
  callback: CallbackCapabilities | null;
  reload: () => void;
}

const FeaturesContext = createContext<FeaturesValue | null>(null);

async function loadFeatures() {
  const [meta, callback] = await Promise.all([
    api.get<Schemas["CapabilitiesResponse"]>("/v1/meta/capabilities"),
    api.get<CallbackCapabilities>("/v1/callback/capabilities").catch(() => null),
  ]);
  return { meta, callback };
}

export function FeaturesProvider({ children }: { children: ReactNode }) {
  const res = useResource("features", loadFeatures);
  const value = useMemo<FeaturesValue>(
    () => ({
      loaded: res.data !== null,
      error: res.error,
      flags: res.data?.meta.capabilities ?? {},
      version: res.data?.meta.version ?? null,
      callback: res.data?.callback ?? null,
      reload: res.reload,
    }),
    [res.data, res.error, res.reload],
  );
  return <FeaturesContext.Provider value={value}>{children}</FeaturesContext.Provider>;
}

export function useFeatures(): FeaturesValue {
  const ctx = useContext(FeaturesContext);
  if (!ctx) throw new Error("useFeatures must be used inside FeaturesProvider");
  return ctx;
}

export const FLAG_LABELS: Record<string, string> = {
  outbound: "Outbound contact",
  live_stt: "Live speech-to-text",
  ai_outbound: "AI outbound voice",
  community: "Community sharing",
  filing_submission: "Electronic filing submission",
};
