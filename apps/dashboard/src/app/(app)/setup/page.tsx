"use client";

import { Badge } from "@/components/Badge";
import { DoctorChecks, DocsList, GatedFeatures } from "@/components/SetupReport";
import { Loadable, Notice, PageHeader, Section } from "@/components/ui";
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";
import { useResource } from "@/lib/hooks";

export default function SetupPage() {
  const res = useResource("setup", () => api.get<Schemas["DoctorOut"]>("/v1/system/doctor"));
  return (
    <>
      <PageHeader
        title="Setup"
        subtitle="Read-only installation checks. Change settings with the CLI or .env, then refresh."
        actions={
          <button type="button" className="btn" onClick={res.reload}>
            Refresh
          </button>
        }
      />
      <Notice tone="warn">
        Admin only. To change anything run <code>callshield init</code> or edit <code>.env</code>, then
        restart the API. Nothing here is legal advice.
      </Notice>
      <Loadable {...res} rows={6}>
        {(report) => (
          <>
            <Section title="Checks">
              <p className="mb-3 text-sm" role="status">
                <Badge tone={report.ok ? "ok" : "bad"}>{report.ok ? "No failures" : "Failures found"}</Badge>{" "}
                {report.counts["PASS"] ?? 0} pass, {report.counts["WARN"] ?? 0} warn, {report.counts["FAIL"] ?? 0} fail,{" "}
                {report.counts["SKIP"] ?? 0} skipped.
              </p>
              <DoctorChecks checks={report.checks} />
            </Section>
            <Section title="Gated features">
              <GatedFeatures features={report.gated_features} />
            </Section>
            <Section title="Documentation">
              <DocsList docs={report.docs} />
            </Section>
          </>
        )}
      </Loadable>
    </>
  );
}
