"use client";

import { useEffect, useState, type ReactNode } from "react";
import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export type Column<T> = {
  key: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  className?: string;
};

/**
 * A server-paginated table for data-heavy pages. It fills its parent's height:
 * the toolbar and pagination stay put, the header sticks, and only the body
 * scrolls (both ways on narrow screens). Pagination is controlled by the parent,
 * which fetches one page at a time.
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  loading,
  error,
  empty,
  toolbar,
  total,
  limit,
  offset,
  onOffset,
  minWidth = 760,
  className,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  loading?: boolean;
  error?: ReactNode;
  empty: ReactNode;
  toolbar?: ReactNode;
  total: number;
  limit: number;
  offset: number;
  onOffset: (offset: number) => void;
  minWidth?: number;
  className?: string;
}) {
  const { t, formatNumber } = useI18n();
  const first = rows.length ? offset + 1 : 0;
  const last = rows.length ? offset + rows.length : 0;
  return (
    <div className={cn("panel flex min-h-0 flex-1 flex-col overflow-hidden", className)}>
      {toolbar && <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">{toolbar}</div>}
      <div className="scrollbar-thin relative min-h-0 flex-1 overflow-auto">
        <table className="w-full border-separate border-spacing-0 text-left text-sm" style={{ minWidth }}>
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={cn(
                    "sticky top-0 z-10 border-b border-border bg-card px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground",
                    column.className,
                  )}
                >
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && !rows.length ? (
              <tr>
                <td colSpan={columns.length} className="p-8 text-center text-sm text-muted-foreground">
                  <span role="status" className="inline-flex items-center gap-2">
                    <Loader2 className="size-4 animate-spin" /> {t.common.loading}
                  </span>
                </td>
              </tr>
            ) : error ? (
              <tr>
                <td colSpan={columns.length} role="alert" className="p-6 text-sm text-destructive">
                  {error}
                </td>
              </tr>
            ) : !rows.length ? (
              <tr>
                <td colSpan={columns.length} className="p-8 text-center text-sm text-muted-foreground">
                  {empty}
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={rowKey(row)} className="hover:bg-surface-2/60">
                  {columns.map((column) => (
                    <td key={column.key} className={cn("border-b border-border px-3 py-2 align-middle", column.className)}>
                      {column.cell(row)}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-3 py-2 text-xs text-muted-foreground">
        <span className="flex items-center gap-2">
          {t.table.range(formatNumber(first), formatNumber(last), formatNumber(total))}
          {loading && rows.length > 0 && <Loader2 className="size-3 animate-spin" />}
        </span>
        <div className="flex gap-1.5">
          <Button
            variant="outline"
            size="sm"
            className="h-7"
            disabled={offset === 0 || loading}
            onClick={() => onOffset(Math.max(0, offset - limit))}
          >
            <ChevronLeft className="size-3.5" /> {t.table.previous}
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7"
            disabled={offset + limit >= total || loading}
            onClick={() => onOffset(offset + limit)}
          >
            {t.table.next} <ChevronRight className="size-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}

/** A search value that settles 300 ms after the last keystroke, so typing does not send a request per key. */
export function useDebounced<T>(value: T, delay = 300): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return settled;
}
