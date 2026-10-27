"use client";

import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from "react";

interface Props {
  open: boolean;
  title: string;
  confirmLabel: string;
  cancelLabel?: string;
  busy?: boolean;
  confirmDisabled?: boolean;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  children: ReactNode;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Modal with a focus trap: Tab cycles inside, Escape cancels, focus returns to the opener. */
export function ConfirmDialog(props: Props) {
  const { open, title, confirmLabel, cancelLabel = "Cancel", busy, danger } = props;
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    const first = panelRef.current?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    return () => opener?.focus();
  }, [open]);

  if (!open) return null;

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.stopPropagation();
      props.onCancel();
      return;
    }
    if (e.key !== "Tab") return;
    const nodes = panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
    if (!nodes || nodes.length === 0) return;
    const firstNode = nodes[0] as HTMLElement;
    const lastNode = nodes[nodes.length - 1] as HTMLElement;
    if (e.shiftKey && document.activeElement === firstNode) {
      e.preventDefault();
      lastNode.focus();
    } else if (!e.shiftKey && document.activeElement === lastNode) {
      e.preventDefault();
      firstNode.focus();
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-4 sm:items-center">
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={onKeyDown}
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg border border-line bg-panel p-5 shadow-xl"
      >
        <h2 id={titleId} className="mb-3 text-lg font-semibold">
          {title}
        </h2>
        <div className="space-y-3 text-sm">{props.children}</div>
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button type="button" className="btn" onClick={props.onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className={`btn ${danger ? "btn-danger" : "btn-primary"}`}
            onClick={props.onConfirm}
            disabled={busy || props.confirmDisabled}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
