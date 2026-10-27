"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { api } from "@/lib/api/client";
import { newIdempotencyKey } from "@/lib/api/idempotency";
import type { CallbackView, Eligibility } from "@/lib/api/types";
import { useAction, useResource } from "@/lib/hooks";
import { Badge } from "./Badge";
import { ConfirmDialog } from "./ConfirmDialog";
import { ErrorBanner } from "./ErrorBanner";
import { GateList, gateTitle } from "./GateList";
import { SkeletonLoader } from "./SkeletonLoader";

const APP_VERSION = "dashboard-0.1.0";

interface Props {
  callId: string;
  incidentId: string | null;
  /** From /v1/callback/capabilities. null = unknown, false = feature OFF. */
  callbackEnabled: boolean | null;
}

function ComplaintOffer({ callId }: { callId: string }) {
  return (
    <p className="text-sm">
      You can still report this call through the complaint flow.{" "}
      <Link className="link" href={`/cases?from_call=${callId}#new-case`}>
        Start a complaint case
      </Link>
    </p>
  );
}

function blockedReason(e: Eligibility): string {
  const failed = e.gates.find((g) => !g.passed);
  if (failed) return `${gateTitle(failed.gate)}: ${failed.message}`;
  return e.caller_id.reason ?? "This call is not eligible for a callback.";
}

interface DialogProps {
  open: boolean;
  busy: boolean;
  error: string | null;
  recording: boolean;
  noticeOk: boolean;
  onRecording: (v: boolean) => void;
  onNotice: (v: boolean) => void;
  onConfirm: () => void;
  onCancel: () => void;
}

function AuthorizeDialog(p: DialogProps) {
  return (
    <ConfirmDialog
      open={p.open}
      title="Authorize one callback"
      confirmLabel="Authorize one callback"
      busy={p.busy}
      confirmDisabled={p.recording && !p.noticeOk}
      onConfirm={p.onConfirm}
      onCancel={p.onCancel}
    >
      <p>You are authorizing one return call to the number that called you. Please confirm you understand:</p>
      <ul className="list-disc space-y-1 pl-5">
        <li>The call will be made by an automated assistant, and it will say so as the first thing it says.</li>
        <li>It is recorded only if you turn recording on below. If you do, the disclosure tells the other party the call is recorded.</li>
        <li>At most 2 attempts are made for this inbound call. Your authorization expires in 30 minutes if dialing has not started.</li>
        <li>The call uses your own verified number as caller ID. You cannot enter or change the number to call.</li>
      </ul>
      <label className="flex items-start gap-2">
        <input type="checkbox" className="mt-1 h-5 w-5" checked={p.recording} onChange={(e) => p.onRecording(e.target.checked)} />
        <span>Record this call (off by default)</span>
      </label>
      {p.recording && (
        <label className="flex items-start gap-2">
          <input type="checkbox" className="mt-1 h-5 w-5" checked={p.noticeOk} onChange={(e) => p.onNotice(e.target.checked)} />
          <span>I confirm the spoken disclosure includes the recording notice.</span>
        </label>
      )}
      {p.error && <ErrorBanner error={p.error} />}
    </ConfirmDialog>
  );
}

function EligibleBody({ callId, incidentId, data }: { callId: string; incidentId: string | null; data: Eligibility }) {
  const [open, setOpen] = useState(false);
  const [recording, setRecording] = useState(false);
  const [noticeOk, setNoticeOk] = useState(false);
  const [queued, setQueued] = useState<CallbackView | null>(null);
  const keyRef = useRef<string | null>(null);
  const action = useAction();

  function openDialog() {
    keyRef.current = newIdempotencyKey("callback");
    action.clear();
    setOpen(true);
  }

  async function confirm() {
    const key = keyRef.current ?? newIdempotencyKey("callback");
    const result = await action.run(() =>
      api.post<CallbackView>(
        `/v1/calls/${callId}/callbacks`,
        {
          app_version: APP_VERSION,
          outbound_recording: recording,
          recording_notice_confirmed: recording && noticeOk,
          incident_id: incidentId,
        },
        key,
      ),
    );
    if (result) {
      setQueued(result);
      setOpen(false);
    }
  }

  if (queued) {
    return (
      <p role="status" className="text-sm">
        Callback queued (status {queued.status}).{" "}
        <Link className="link" href={`/callbacks/${queued.id}`}>View callback</Link>
      </p>
    );
  }
  return (
    <>
      <p className="mb-3 text-sm">All gates pass for this call. Nothing is dialed until you authorize it.</p>
      <button type="button" className="btn btn-primary" onClick={openDialog} disabled={!data.allowed}>
        Authorize one callback
      </button>
      <AuthorizeDialog
        open={open}
        busy={action.pending}
        error={action.error}
        recording={recording}
        noticeOk={noticeOk}
        onRecording={setRecording}
        onNotice={setNoticeOk}
        onConfirm={() => void confirm()}
        onCancel={() => setOpen(false)}
      />
    </>
  );
}

function BlockedBody({ callId, data }: { callId: string; data: Eligibility }) {
  return (
    <div className="space-y-2">
      <button type="button" className="btn" disabled aria-describedby="callback-blocked-reason">
        Authorize one callback
      </button>
      <p id="callback-blocked-reason" className="text-sm font-medium text-bad">
        Callback blocked. {blockedReason(data)}
      </p>
      <ComplaintOffer callId={callId} />
    </div>
  );
}

export function CallbackPanel({ callId, incidentId, callbackEnabled }: Props) {
  const res = useResource(callbackEnabled ? `elig-${callId}` : null, () =>
    api.get<Eligibility>(`/v1/calls/${callId}/callback-eligibility`),
  );
  return (
    <section aria-labelledby="callback-h" className="card mb-5">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 id="callback-h" className="text-lg font-semibold">Callback eligibility</h2>
        {callbackEnabled === false && <Badge>FEATURE OFF</Badge>}
      </div>
      {callbackEnabled === false && (
        <div className="space-y-2 text-sm">
          <p>Automated callbacks are turned off on this server, so no callback can be requested.</p>
          <ComplaintOffer callId={callId} />
        </div>
      )}
      {callbackEnabled && res.loading && !res.data && <SkeletonLoader rows={4} label="Checking eligibility" />}
      {callbackEnabled && res.error !== null && !res.data ? <ErrorBanner error={res.error} onRetry={res.reload} /> : null}
      {callbackEnabled && res.data && (
        <div className="space-y-4">
          <GateList gates={res.data.gates} />
          {res.data.allowed ? (
            <EligibleBody callId={callId} incidentId={incidentId} data={res.data} />
          ) : (
            <BlockedBody callId={callId} data={res.data} />
          )}
        </div>
      )}
    </section>
  );
}
