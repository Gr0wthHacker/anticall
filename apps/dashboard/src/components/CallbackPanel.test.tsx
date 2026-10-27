import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Eligibility } from "@/lib/api/types";
import { headerOf, mockFetch } from "@/test/helpers";
import { CallbackPanel } from "./CallbackPanel";

const GATES = [
  "INBOUND_RECORD",
  "ATTESTATION_A",
  "WITHIN_WINDOW",
  "USER_AUTHORIZATION",
  "NOT_BLOCKLISTED",
  "CAPS",
  "CALLING_HOURS",
];

function eligibility(failing: string[] = []): Eligibility {
  const gates = GATES.map((gate) => ({
    gate,
    passed: !failing.includes(gate),
    reason: failing.includes(gate) ? "FAILED" : "OK",
    message: failing.includes(gate) ? "Attestation is B, not A." : "OK",
    details: {},
  }));
  const allowed = failing.length === 0;
  return {
    allowed,
    complaint_flow_offered: !allowed,
    complaint_flow_only: failing.includes("ATTESTATION_A"),
    blocked_by: failing,
    gates,
    caller_id: { ok: true, reason: null },
    complaint_flow: { offered: !allowed, start: "/v1/cases", inbound_call_id: "call-1" },
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("CallbackPanel", () => {
  it("blocked: lists all 7 gates, disables the button, shows the reason, offers the complaint flow", async () => {
    mockFetch({ "GET /v1/calls/call-1/callback-eligibility": { body: eligibility(["ATTESTATION_A"]) } });
    render(<CallbackPanel callId="call-1" incidentId={null} callbackEnabled />);
    const list = await screen.findByRole("list", { name: /eligibility gates/i });
    expect(within(list).getAllByRole("listitem")).toHaveLength(7);
    expect(within(list).getAllByText("FAIL", { exact: false })).toHaveLength(1);
    const button = screen.getByRole("button", { name: "Authorize one callback" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(/Attestation is B, not A/);
    expect(screen.getByRole("link", { name: /complaint case/i })).toBeInTheDocument();
  });

  it("eligible: dialog restates the disclosure, then POSTs once with an Idempotency-Key and no number", async () => {
    const fetchMock = mockFetch({
      "GET /v1/calls/call-1/callback-eligibility": { body: eligibility() },
      "POST /v1/calls/call-1/callbacks": { status: 202, body: { id: "cb-9", status: "QUEUED" } },
    });
    render(<CallbackPanel callId="call-1" incidentId="inc-1" callbackEnabled />);
    fireEvent.click(await screen.findByRole("button", { name: "Authorize one callback" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/automated assistant/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/recorded only if you turn recording on/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/At most 2 attempts/i)).toBeInTheDocument();
    expect(within(dialog).queryByRole("textbox")).toBeNull();
    fireEvent.click(within(dialog).getByRole("button", { name: "Authorize one callback" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Callback queued"));
    const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts).toHaveLength(1);
    const init = posts[0]?.[1];
    expect(headerOf(init, "Idempotency-Key")).toMatch(/^callback-/);
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(body).toEqual({
      app_version: "dashboard-0.1.0",
      outbound_recording: false,
      recording_notice_confirmed: false,
      incident_id: "inc-1",
    });
  });

  it("feature off: no eligibility request, no button, complaint flow offered", () => {
    const fetchMock = mockFetch({});
    render(<CallbackPanel callId="call-1" incidentId={null} callbackEnabled={false} />);
    expect(screen.getByText(/turned off on this server/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Authorize one callback" })).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
