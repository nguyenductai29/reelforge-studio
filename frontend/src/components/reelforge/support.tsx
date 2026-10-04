"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { Loader2, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/lib/i18n";
import type { SupportCategory, SupportMessage, SupportStatus, SupportTicketDetail } from "@/lib/types";
import { cn } from "@/lib/utils";
import { FieldLabel, StatusBadge } from "./primitives";

export const SUPPORT_CATEGORIES: SupportCategory[] = [
  "billing", "credits", "generation", "publishing", "account", "storage", "bug", "other",
];
export const SUPPORT_STATUSES: SupportStatus[] = ["open", "waiting_support", "waiting_user", "resolved", "closed"];
export type SupportContext = Partial<Record<"run_id" | "project_id" | "payment_order_id" | "publication_id", string>>;

export function SupportStatusBadge({ status }: { status: SupportStatus }) {
  const { t } = useI18n();
  return <StatusBadge status={status} label={t.support.statuses[status]} />;
}

/** The conversation, oldest first; the user's side on the right, support on the left. */
export function SupportThread({ messages, viewer }: { messages: SupportMessage[]; viewer: "user" | "admin" }) {
  const { t, formatDateTime } = useI18n();
  return (
    <div className="space-y-3">
      {messages.map((message) => {
        const mine = message.author_type === viewer;
        return (
          <div key={message.id} className={cn("flex", mine ? "justify-end" : "justify-start")}>
            <div
              className={cn(
                "max-w-[85%] rounded-xl border px-3.5 py-2.5 text-sm",
                message.author_type === "admin"
                  ? "border-primary/30 bg-[color-mix(in_oklab,var(--primary)_8%,transparent)]"
                  : "border-border bg-surface-2",
              )}
            >
              <p className="mb-1 text-[11px] text-muted-foreground">
                {message.author_type === "admin" ? (message.author_email ?? t.support.supportTeam) : (message.author_email ?? t.support.you)}
                {message.created_at ? ` · ${formatDateTime(message.created_at)}` : ""}
              </p>
              <p className="whitespace-pre-wrap break-words leading-relaxed">{message.body}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Reply box under a thread; a closed ticket takes no reply. */
export function SupportReply({ ticket, onSend, extra }: { ticket: SupportTicketDetail;
  onSend: (body: string) => Promise<boolean>; extra?: ReactNode }) {
  const { t } = useI18n();
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  if (ticket.status === "closed") return <p className="text-sm text-muted-foreground">{t.support.closedHint}</p>;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!body.trim()) return;
    setBusy(true);
    if (await onSend(body.trim())) setBody("");
    setBusy(false);
  }

  return (
    <form onSubmit={submit} className="space-y-2">
      <Textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={3}
        maxLength={5000}
        placeholder={t.support.replyPlaceholder}
        aria-label={t.support.replyPlaceholder}
        className="resize-y bg-surface"
      />
      <div className="flex flex-wrap items-center justify-end gap-2">
        {extra}
        <Button type="submit" disabled={busy || !body.trim()}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />} {t.support.send}
        </Button>
      </div>
    </form>
  );
}

/** A new request: subject, category, description, plus any context IDs the page that opened it passed along. */
export function NewTicketDialog({ open, onOpenChange, context, onCreate }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  context: SupportContext;
  onCreate: (input: { subject: string; category: SupportCategory; description: string } & SupportContext) => Promise<void>;
}) {
  const { t } = useI18n();
  const s = t.support;
  const [subject, setSubject] = useState("");
  const [category, setCategory] = useState<SupportCategory>(context.payment_order_id ? "billing" : context.publication_id
    ? "publishing" : context.run_id ? "generation" : "other");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const attached = Object.keys(context) as (keyof SupportContext)[];

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await onCreate({ subject: subject.trim(), category, description: description.trim(), ...context });
      setSubject("");
      setDescription("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{s.newRequest}</DialogTitle>
          <DialogDescription>{s.newRequestHint}</DialogDescription>
        </DialogHeader>
        <form id="support-new" onSubmit={submit} className="space-y-4">
          <div>
            <FieldLabel htmlFor="support-subject">{s.subject}</FieldLabel>
            <Input id="support-subject" value={subject} maxLength={200} required onChange={(e) => setSubject(e.target.value)}
                   className="bg-surface" />
          </div>
          <div>
            <FieldLabel>{s.category}</FieldLabel>
            <Select value={category} onValueChange={(value) => setCategory(value as SupportCategory)}>
              <SelectTrigger className="bg-surface" aria-label={s.category}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SUPPORT_CATEGORIES.map((item) => (
                  <SelectItem key={item} value={item}>
                    {s.categories[item]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <FieldLabel htmlFor="support-description">{s.description}</FieldLabel>
            <Textarea id="support-description" value={description} rows={5} maxLength={5000} required
                      onChange={(e) => setDescription(e.target.value)} className="resize-y bg-surface" />
            <p className="mt-1 text-[11px] text-muted-foreground">{s.privacyHint}</p>
          </div>
          {attached.length > 0 && (
            <p className="text-xs text-muted-foreground">
              {s.attached}: {attached.map((key) => `${s.contextLabels[key]} ${context[key]!.slice(0, 8)}`).join(" · ")}
            </p>
          )}
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t.common.cancel}
          </Button>
          <Button type="submit" form="support-new" disabled={busy || !subject.trim() || !description.trim()}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {s.submit}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
