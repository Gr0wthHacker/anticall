import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Schemas } from "@/lib/api/types";
import { DoctorChecks, GatedFeatures } from "./SetupReport";

const checks: Schemas["DoctorOut"]["checks"] = [
  { id: "database", title: "Database reachable", status: "PASS", detail: "connected", fix: "" },
  { id: "secrets", title: "Secrets set", status: "FAIL", detail: "weak", fix: "Run callshield init" },
  { id: "ari", title: "ARI", status: "SKIP", detail: "ARI disabled", fix: "Not shown for skip" },
];

describe("DoctorChecks", () => {
  it("shows status text and a fix hint only for WARN/FAIL", () => {
    render(<DoctorChecks checks={checks} />);
    expect(screen.getByText("PASS")).toBeInTheDocument();
    expect(screen.getByText("FAIL")).toBeInTheDocument();
    expect(screen.getByText("Run callshield init")).toBeInTheDocument();
    expect(screen.queryByText("Not shown for skip")).not.toBeInTheDocument();
  });
});

describe("GatedFeatures", () => {
  const feature = (enabled: boolean): Schemas["DoctorOut"]["gated_features"][number] => ({
    key: "callback",
    env_var: "CALLSHIELD_CALLBACK_CALLBACK_ENABLED",
    title: "Automated callback",
    enabled,
    gates: "G1-G5",
    warning: "Legally unresolved.",
  });

  it("says everything is off when nothing is enabled", () => {
    render(<GatedFeatures features={[feature(false)]} />);
    expect(screen.getByRole("status")).toHaveTextContent("All gated features are off.");
    expect(screen.getByText("off")).toBeInTheDocument();
  });

  it("flags enabled features loudly", () => {
    render(<GatedFeatures features={[feature(true)]} />);
    expect(screen.getByRole("status")).toHaveTextContent("1 gated feature(s) are ON.");
    expect(screen.getByText("ON")).toBeInTheDocument();
  });
});
