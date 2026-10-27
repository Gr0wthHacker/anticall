import type { components } from "./schema";

/** Typed schemas from the generated OpenAPI file. */
export type Schemas = components["schemas"];

export type EvidenceLabelName = Schemas["EvidenceLabel"];
export const EVIDENCE_LABELS: readonly EvidenceLabelName[] = [
  "OBSERVED",
  "CLAIMED",
  "MODEL_INFERRED",
  "USER_ATTESTED",
  "EXTERNALLY_VERIFIED",
];

/* Hand-written shapes for endpoints the OpenAPI spec leaves as free-form objects. */

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ClassificationView {
  id?: string;
  label: string;
  confidence?: number | null;
  model_version?: string;
  evidence_label: string;
  reviewed_status?: string;
  [key: string]: unknown;
}

export interface CallDetail {
  id: string;
  line_id: string | null;
  incident_id: string | null;
  facts: {
    evidence_label: string;
    provider: string;
    direction: string;
    disposition: string;
    started_at: string | null;
    ended_at: string | null;
    duration_seconds: number | null;
    displayed_caller_id: string | null;
    network_attestation: Record<string, unknown>;
    note: string;
  };
  claims: {
    id: string;
    statement_type: string;
    claimed: string;
    evidence_label: string;
    verification_status: string;
  }[];
  model_conclusions: ClassificationView[];
  effective_classification: ClassificationView | null;
  routing: {
    action: string;
    reason: string;
    processing_mode: string;
    screening_active: boolean;
  } | null;
}

export interface GateResult {
  gate: string;
  passed: boolean;
  reason: string;
  message: string;
  details: Record<string, unknown>;
}

export interface Eligibility {
  allowed: boolean;
  complaint_flow_offered: boolean;
  complaint_flow_only: boolean;
  blocked_by: string[];
  gates: GateResult[];
  caller_id: { ok: boolean; reason: string | null };
  complaint_flow: { offered: boolean; start: string; inbound_call_id: string };
}

export interface CallbackResultView {
  outcome: string;
  fields: Record<string, unknown>;
  metadata: Record<string, unknown>;
  tombstoned: boolean;
  retention_expires_at: string;
}

export interface CallbackView {
  id: string;
  inbound_call_id: string;
  line_id: string;
  status: string;
  destination: string;
  outbound_recording: boolean;
  incident_id: string | null;
  case_id: string | null;
  created_at: string;
  updated_at: string;
  result?: CallbackResultView;
  gate_report?: Record<string, unknown>;
  replayed?: boolean;
}

export type SettingChanger = "user" | "operator" | "locked" | "per_call";

export interface CallbackSettingsBody {
  settings: Record<string, number | boolean | string>;
  timezone: string | null;
  changeable_by: Record<string, SettingChanger>;
}

export interface CallbackCapabilities {
  callback_enabled: boolean;
  ceilings: Record<string, number>;
  remote_ai_processing: boolean;
}

export interface SufficiencyItem {
  key: string;
  status: string;
  weight?: number;
  [key: string]: unknown;
}

export interface IncidentLinkView {
  incident_id: string;
  linked: boolean;
  linkage_type: string;
  evidence_basis: Record<string, unknown>;
  actor: string;
  updated_at: string;
  effective_label: string;
  evidence_label: string;
}

export interface CaseDetailView {
  case: Schemas["callshield__api__routers__cases__CaseOut"];
  incident_links: IncidentLinkView[];
  evidence_sufficiency: {
    items: SufficiencyItem[];
    missing_sources: string[];
    partial_sources: string[];
    present_weight: number;
    total_weight: number;
    disclaimer: string;
  };
  repeat_contact_verified: boolean;
  possible_subsequent_incident_ids: string[];
  available_transitions: string[];
  match_ruleset_version: string;
}

export interface TimelineEntry {
  entry_id: string;
  kind: string;
  occurred_at: string;
  time_basis: string;
  source_label: string;
  summary: string;
  source_ids: Record<string, string>;
  details: Record<string, unknown>;
}

export interface CaseTimeline {
  case_id: string;
  count: number;
  entries: TimelineEntry[];
}

export interface EvidenceArtifact {
  id: string;
  case_id: string | null;
  incident_id: string | null;
  source_type: string;
  media_type: string;
  sha256: string;
  byte_length: number;
  origin: string;
  evidence_label: string;
  collection_method: string;
  collected_at: string;
  processing_authorization: string;
  sharing_authorization: string;
  redaction_state: string;
  derived_from_id: string | null;
  supersedes_id: string | null;
  version: number;
  provenance: Record<string, unknown>;
  expires_at: string | null;
  legal_hold: boolean;
  deleted_at: string | null;
  content_available: boolean;
}

export interface ManualHandoff {
  destination?: string;
  official_url?: string;
  steps?: string[];
  notices?: string[];
}

export interface PreparedSubmission {
  destination_name: string;
  method: string;
  official_url: string;
  steps: string[];
  copyable_text: string;
  fields: Record<string, unknown>;
  payload_digest: string;
  notices: string[];
}

export interface UnsupportedDestinationDetails {
  destination?: string;
  manual_handoff?: ManualHandoff;
  prepared_submission?: PreparedSubmission;
  approval_status?: string;
  filing_id?: string;
}

export type LineView = Schemas["LineOut"];
