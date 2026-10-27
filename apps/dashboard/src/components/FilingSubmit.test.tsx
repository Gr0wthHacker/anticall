import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Schemas } from "@/lib/api/types";
import { headerOf, mockFetch } from "@/test/helpers";
import { FilingSubmit } from "./FilingSubmit";

const draft = {
  draft_id: "d-1",
  case_id: "case-1",
  revision: 1,
  kind: "COMPLAINT",
  agency: "FCC",
  destination_slug: "fcc-unwanted-call",
  template_version: "t1",
  review_state: "APPROVED",
  payload: {},
  payload_digest: "abc123digest",
  evidence_snapshot_digest: "snap",
  sources: [],
  exclusions: [],
  completeness: {},
  export_scope: {},
  supersedes_draft_id: null,
  supplements_filing_id: null,
  notices: [],
  created_at: "2026-10-27T00:00:00Z",
  attestation: {
    version: "v1",
    must_display_before_accept: ["complaint_text"],
    statements: [{ id: "reviewed_exact_content", required: true, text: "I reviewed it." }],
  },
} as unknown as Schemas["DraftOut"];

afterEach(() => vi.unstubAllGlobals());

const UNSUPPORTED = {
  error: {
    code: "UNSUPPORTED_DESTINATION",
    message: "no supported electronic submission",
    details: {
      manual_handoff: {
        official_url: "https://example.gov/complaint",
        steps: ["Open the form", "Paste the text"],
      },
      prepared_submission: {
        destination_name: "FCC",
        method: "manual_handoff",
        official_url: "https://example.gov/complaint",
        steps: [],
        copyable_text: "PREPARED TEXT BODY",
        fields: {},
        payload_digest: "abc123digest",
        notices: [],
      },
    },
  },
};

describe("FilingSubmit", () => {
  it("422 UNSUPPORTED_DESTINATION shows the manual handoff and a copyable prepared submission", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const fetchMock = mockFetch({
      "POST /v1/filing-drafts/d-1/submit": { status: 422, body: UNSUPPORTED },
    });
    const onSubmitted = vi.fn();
    render(<FilingSubmit draft={draft} onSubmitted={onSubmitted} />);
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    const panel = await screen.findByTestId("manual-handoff");
    expect(within(panel).getByText(/cannot be filed electronically/i)).toBeInTheDocument();
    expect(within(panel).getByText("Open the form")).toBeInTheDocument();
    expect(within(panel).getByText("PREPARED TEXT BODY")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(within(panel).getByRole("button", { name: /copy prepared submission/i }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("PREPARED TEXT BODY"));
    expect(onSubmitted).not.toHaveBeenCalled();
    expect(headerOf(fetchMock.mock.calls[0]?.[1], "Idempotency-Key")).toMatch(/^submit-/);
  });

  it("other errors surface as an alert and never as a handoff", async () => {
    mockFetch({
      "POST /v1/filing-drafts/d-1/submit": {
        status: 409,
        body: { error: { code: "APPROVAL_VOID", message: "approval no longer matches", details: {} } },
      },
    });
    render(<FilingSubmit draft={draft} />);
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("approval no longer matches");
    expect(screen.queryByTestId("manual-handoff")).toBeNull();
  });

  it("keeps Submit disabled until the draft is approved", () => {
    render(<FilingSubmit draft={{ ...draft, review_state: "DRAFT" } as Schemas["DraftOut"]} />);
    expect(screen.getByRole("button", { name: "Submit" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /approve this exact draft/i })).toBeDisabled();
  });
});
