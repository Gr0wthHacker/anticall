"use client";

import { FlagBadge } from "@/components/Badge";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/ui";
import { useFeatures } from "@/lib/features";

export default function CampaignsPage() {
  const { flags, loaded } = useFeatures();
  return (
    <>
      <PageHeader title="Campaigns" subtitle="Community sharing of call reports. Planned for after the first release." />
      <p className="mb-4 flex items-center gap-2 text-sm">Community sharing on this server: {loaded ? <FlagBadge on={flags["community"] === true} /> : "checking…"}</p>
      <EmptyState
        title="Community sharing is off and is a post-MVP feature"
        hint="Nothing is shared with other people, and no community data appears here. This page is intentionally empty."
      />
    </>
  );
}
