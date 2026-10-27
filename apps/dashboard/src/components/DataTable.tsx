import Link from "next/link";
import type { ReactNode } from "react";
import { EmptyState } from "./EmptyState";

export interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
}

interface Props<T> {
  caption: string;
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  rowHref?: (row: T) => string;
  emptyTitle?: string;
  emptyHint?: string;
}

/** Semantic table. On narrow screens it scrolls horizontally inside its own region. */
export function DataTable<T>(props: Props<T>) {
  const { caption, columns, rows, rowKey, rowHref } = props;
  if (rows.length === 0) {
    return <EmptyState title={props.emptyTitle ?? "Nothing here yet"} hint={props.emptyHint} />;
  }
  return (
    <div
      className="overflow-x-auto rounded-lg border border-line"
      role="region"
      aria-label={caption}
      tabIndex={0}
    >
      <table className="w-full min-w-[34rem] border-collapse text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-surface text-muted">
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className="px-3 py-2 font-medium">
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line bg-panel">
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((c, i) => (
                <td key={c.key} className="px-3 py-2 align-top">
                  {i === 0 && rowHref ? (
                    <Link className="link" href={rowHref(row)}>
                      {c.render(row)}
                    </Link>
                  ) : (
                    c.render(row)
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
