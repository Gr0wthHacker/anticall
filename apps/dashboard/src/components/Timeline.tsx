import type { ReactNode } from "react";
import { formatDateTime } from "@/lib/format";
import { EvidenceBadge } from "./Badge";

export interface TimelineItem {
  id: string;
  at: string | null;
  label?: string | null;
  title: string;
  meta?: string;
  children?: ReactNode;
}

/** Chronological list; every item carries its source label badge. */
export function Timeline({ items, ariaLabel }: { items: TimelineItem[]; ariaLabel: string }) {
  return (
    <ol className="space-y-3 border-l-2 border-line pl-4" aria-label={ariaLabel}>
      {items.map((item) => (
        <li key={item.id} className="relative">
          <span
            aria-hidden="true"
            className="absolute -left-[22px] top-1.5 h-3 w-3 rounded-full border-2 border-brand bg-panel"
          />
          <div className="flex flex-wrap items-center gap-2">
            <time className="text-xs text-muted" dateTime={item.at ?? undefined}>
              {formatDateTime(item.at)}
            </time>
            {item.label !== undefined && <EvidenceBadge label={item.label} />}
            {item.meta && <span className="text-xs text-muted">{item.meta}</span>}
          </div>
          <p className="text-sm">{item.title}</p>
          {item.children}
        </li>
      ))}
    </ol>
  );
}
