"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { QueryError } from "@/components/reelforge/query-state";
import { Button } from "@/components/ui/button";
import { SupportReply, SupportStatusBadge, SupportThread } from "@/components/reelforge/support";
import { ApiError, api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { keys, useSupportTicket } from "@/lib/queries";
import type { SupportTicketDetail } from "@/lib/types";

export default function SupportTicketPage() {
  const { t, formatDateTime } = useI18n();
  const s = t.support;
  const { ticketId } = useParams<{ ticketId: string }>();
  const client = useQueryClient();
  const showError = useErrorToast();
  const ticket = useSupportTicket(ticketId);
  useDocumentTitle(ticket.data?.subject ?? s.title);

  async function act(path: string, body: unknown, done?: string) {
    try {
      const updated = await api<SupportTicketDetail>(`support/tickets/${encodeURIComponent(ticketId)}/${path}`,
                                                     jsonRequest("POST", body));
      client.setQueryData([...keys.support, "ticket", ticketId], updated);
      await client.invalidateQueries({ queryKey: [...keys.support, "list"] });
      if (done) toast.success(done);
      return true;
    } catch (error) {
      showError(error);
      return false;
    }
  }

  if (ticket.isPending) return <Loader2 className="mx-auto mt-10 size-5 animate-spin text-muted-foreground" />;
  if (!ticket.data && ticket.isError && !(ticket.error instanceof ApiError && ticket.error.status === 404)) {
    return <QueryError error={ticket.error} onRetry={() => void ticket.refetch()} />;
  }
  if (!ticket.data) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">{s.notFound}</p>
        <Button asChild variant="outline" size="sm">
          <Link href="/support">{s.back}</Link>
        </Button>
      </div>
    );
  }
  const data = ticket.data;
  return (
    <div className="max-w-3xl space-y-5">
      <Link href="/support" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> {s.back}
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold">{data.subject}</h1>
          <p className="mt-1 text-xs text-muted-foreground">
            #{data.id.slice(0, 8)} · {s.categories[data.category]}
            {data.created_at ? ` · ${formatDateTime(data.created_at)}` : ""}
          </p>
        </div>
        <SupportStatusBadge status={data.status} />
      </div>
      <div className="panel space-y-5 p-4">
        <SupportThread messages={data.thread} viewer="user" />
        <SupportReply
          ticket={data}
          onSend={(body) => act("messages", { body })}
          extra={
            <Button type="button" variant="ghost" onClick={() => void act("close", {}, s.closed)}>
              {s.close}
            </Button>
          }
        />
      </div>
    </div>
  );
}
