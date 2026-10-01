"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { LifeBuoy, MessageSquare, Plus } from "lucide-react";
import { QueryError } from "@/components/reelforge/query-state";
import { Button } from "@/components/ui/button";
import { EmptyState, PageHeader } from "@/components/reelforge/primitives";
import { NewTicketDialog, SupportStatusBadge, type SupportContext } from "@/components/reelforge/support";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { keys, useSupportTickets } from "@/lib/queries";
import type { SupportTicketDetail } from "@/lib/types";

const PAGE = 20;
const CONTEXT_KEYS = ["run_id", "project_id", "payment_order_id", "publication_id"] as const;

function SupportPage() {
  const { t, formatRelative } = useI18n();
  const s = t.support;
  useDocumentTitle(s.title);
  const router = useRouter();
  const search = useSearchParams();
  const client = useQueryClient();
  const showError = useErrorToast();
  // A page that hit a problem can link here with ?new=1&run_id=… (safe IDs only).
  const context: SupportContext = {};
  for (const key of CONTEXT_KEYS) {
    const value = search.get(key);
    if (value && /^[A-Za-z0-9-]{1,36}$/.test(value)) context[key] = value;
  }
  const [creating, setCreating] = useState(search.get("new") === "1");
  const [offset, setOffset] = useState(0);
  const tickets = useSupportTickets(offset, PAGE);
  const items = tickets.data?.items ?? [];
  const total = tickets.data?.total ?? 0;

  async function create(input: Record<string, string>) {
    try {
      const ticket = await api<SupportTicketDetail>("support/tickets", jsonRequest("POST", input));
      await client.invalidateQueries({ queryKey: keys.support });
      setCreating(false);
      router.push(`/support/${ticket.id}`);
    } catch (error) {
      showError(error);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={s.title}
        subtitle={s.subtitle}
        actions={
          <Button onClick={() => setCreating(true)}>
            <Plus className="size-4" /> {s.newRequest}
          </Button>
        }
      />
      {tickets.isError && !items.length ? (
        <QueryError error={tickets.error} onRetry={() => void tickets.refetch()} className="max-w-4xl" />
      ) : !items.length && !tickets.isPending ? (
        <EmptyState icon={LifeBuoy} title={s.empty} description={s.emptyHint} />
      ) : (
        <div className="panel max-w-4xl divide-y divide-border">
          {items.map((ticket) => (
            <Link
              key={ticket.id}
              href={`/support/${ticket.id}`}
              className="flex flex-wrap items-center gap-3 p-4 transition-colors hover:bg-surface-2"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{ticket.subject}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {s.categories[ticket.category]} · #{ticket.id.slice(0, 8)}
                  {ticket.updated_at ? ` · ${formatRelative(ticket.updated_at)}` : ""}
                </p>
              </div>
              <span className="flex items-center gap-1 text-xs text-muted-foreground">
                <MessageSquare className="size-3.5" /> {ticket.messages ?? 0}
              </span>
              <SupportStatusBadge status={ticket.status} />
            </Link>
          ))}
        </div>
      )}
      {total > PAGE && (
        <div className="flex max-w-4xl items-center justify-end gap-1.5">
          <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            {t.table.previous}
          </Button>
          <Button variant="outline" size="sm" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
            {t.table.next}
          </Button>
        </div>
      )}
      <NewTicketDialog open={creating} onOpenChange={setCreating} context={context} onCreate={create} />
    </div>
  );
}

export default function Page() {
  return (
    <Suspense>
      <SupportPage />
    </Suspense>
  );
}
