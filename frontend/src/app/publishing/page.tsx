"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CalendarClock, ExternalLink, Loader2, RotateCcw, Send, XCircle } from "lucide-react";
import { toast } from "sonner";
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
import { FieldLabel } from "@/components/reelforge/primitives";
import { PageHeader, PlatformIcon, StatusBadge, platformLabel } from "@/components/reelforge/primitives";
import { ChannelBadge, ScheduleDialog } from "@/components/reelforge/publication-actions";
import { PublishDialog, splitTags, tagsLength } from "@/components/reelforge/publish-dialog";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { keys, useChannels, usePublicationsPage } from "@/lib/queries";
import type { PrivacyStatus, Publication } from "@/lib/types";

const PAGE = 20;
const PRIVACY: Record<Publication["channel"], PrivacyStatus[]> = {
  youtube: ["private", "unlisted", "public"],
  tiktok: ["private"],
  facebook: ["private", "public"],
};

/**
 * Retries a failed upload that never sent media: only a new upload job is queued, from the same video.
 * The metadata can be corrected first (for example a title YouTube rejected).
 */
function RetryDialog({ publication, onClose }: { publication: Publication | null; onClose: () => void }) {
  const { t } = useI18n();
  const d = t.publishing.dialog;
  const client = useQueryClient();
  const showError = useErrorToast();
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<{ title: string; description: string; tags: string; privacy: PrivacyStatus } | null>(
    null,
  );
  const current = form ?? {
    title: publication?.title ?? "",
    description: publication?.description ?? "",
    tags: (publication?.tags ?? []).join(", "),
    privacy: publication?.privacy_status ?? "private",
  };

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!publication) return;
    setBusy(true);
    try {
      await api(
        `publications/${encodeURIComponent(publication.id)}/retry`,
        jsonRequest("POST", {
          title: current.title.trim(),
          description: current.description,
          tags: splitTags(current.tags),
          privacy_status: current.privacy,
        }),
      );
      await client.invalidateQueries({ queryKey: keys.publications });
      toast.success(t.publishing.retried);
      setForm(null);
      onClose();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={Boolean(publication)}
      onOpenChange={(open) => {
        if (!open) {
          setForm(null);
          onClose();
        }
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t.publishing.retryTitle}</DialogTitle>
          <DialogDescription>{t.publishing.retryDescription}</DialogDescription>
        </DialogHeader>
        <form id="retry-form" onSubmit={submit} className="space-y-4">
          <div>
            <FieldLabel htmlFor="retry-title">{d.titleLabel}</FieldLabel>
            <Input
              id="retry-title"
              value={current.title}
              maxLength={100}
              required
              onChange={(e) => setForm({ ...current, title: e.target.value.replace(/[\r\n]/g, " ") })}
              className="bg-surface"
            />
          </div>
          <div>
            <FieldLabel htmlFor="retry-description">{d.descriptionLabel}</FieldLabel>
            <Textarea
              id="retry-description"
              rows={3}
              maxLength={5000}
              value={current.description}
              onChange={(e) => setForm({ ...current, description: e.target.value })}
              className="resize-none bg-surface"
            />
          </div>
          <div>
            <FieldLabel htmlFor="retry-tags">{d.tagsLabel}</FieldLabel>
            <Input
              id="retry-tags"
              value={current.tags}
              onChange={(e) => setForm({ ...current, tags: e.target.value })}
              className="bg-surface"
            />
            <p className="mt-1 text-[11px] text-muted-foreground">{d.tagsHint(tagsLength(splitTags(current.tags)))}</p>
          </div>
          <div>
            <FieldLabel>{d.privacyLabel}</FieldLabel>
            <Select value={current.privacy} onValueChange={(value) => setForm({ ...current, privacy: value as PrivacyStatus })}>
              <SelectTrigger className="bg-surface" aria-label={d.privacyLabel}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PRIVACY[publication?.channel ?? "youtube"].map((value) => (
                  <SelectItem key={value} value={value}>
                    {publication?.channel === "facebook"
                      ? d.facebookPrivacy[value as "public" | "private"]
                      : t.publishing.privacy[value]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            {t.common.cancel}
          </Button>
          <Button type="submit" form="retry-form" disabled={busy || !current.title.trim()}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {t.publishing.retry}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function PublishingPage() {
  const { t, formatDateTime } = useI18n();
  useDocumentTitle(t.publishing.title);
  const channels = useChannels().data ?? [];
  const status = (channel: Publication["channel"]) => channels.find((c) => c.channel === channel);
  const [offset, setOffset] = useState(0);
  const page = usePublicationsPage(offset, PAGE);
  const publications = page.data?.publications ?? [];
  const total = page.data?.total ?? 0;
  const [retrying, setRetrying] = useState<Publication | null>(null);
  const [scheduling, setScheduling] = useState<{ publication: Publication; mode: "reschedule" | "cancel" } | null>(null);

  return (
    <div className="space-y-8">
      <PageHeader
        title={t.publishing.title}
        subtitle={t.publishing.subtitle}
        actions={
          <PublishDialog
            trigger={
              <Button>
                <Send className="size-4" /> {t.publishing.publishContent}
              </Button>
            }
          />
        }
      />

      <section>
        <h2 className="mb-4 text-base font-semibold">{t.publishing.connectedChannels}</h2>
        <div className="grid gap-4 sm:grid-cols-3">
          {(["youtube", "tiktok", "facebook"] as const).map((channel) => {
            const state = status(channel);
            return (
              <div key={channel} className="panel space-y-3 p-4">
                <div className="flex items-center justify-between">
                  <span className="flex size-9 items-center justify-center rounded-lg bg-surface-2">
                    <PlatformIcon platform={channel} />
                  </span>
                  <ChannelBadge status={state?.status} />
                </div>
                <div>
                  <p className="text-sm font-medium">{platformLabel[channel]}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    {state?.account_name
                      ? t.channels.accountAs(state.account_name)
                      : state?.status === "connected"
                        ? t.status.channel.connected
                        : t.channels.notLinked}
                  </p>
                </div>
                <Button asChild variant="outline" size="sm" className="w-full">
                  <Link href="/channels">{state?.status === "connected" ? t.common.manage : t.publishing.connect}</Link>
                </Button>
              </div>
            );
          })}
        </div>
      </section>

      <section>
        <h2 className="mb-4 text-base font-semibold">{t.publishing.queue}</h2>
        <div className="panel divide-y divide-border">
          <div className="hidden grid-cols-[2fr_1fr_1fr_auto] gap-4 px-4 py-3 text-xs uppercase tracking-wider text-muted-foreground sm:grid">
            <span>{t.publishing.columns.content}</span>
            <span>{t.publishing.columns.channel}</span>
            <span>{t.publishing.columns.time}</span>
            <span>{t.publishing.columns.status}</span>
          </div>
          {publications.length === 0 && <p className="px-4 py-5 text-sm text-muted-foreground">{t.publishing.queueEmpty}</p>}
          {publications.map((publication) => (
            <div
              key={publication.id}
              className="grid gap-2 px-4 py-3 text-sm sm:grid-cols-[2fr_1fr_1fr_auto] sm:items-center sm:gap-4"
            >
              <div className="min-w-0">
                <p className="truncate font-medium">{publication.title}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {t.publishing.privacy[publication.privacy_status ?? "private"]}
                  {publication.tags?.length ? ` · ${publication.tags.map((tag) => `#${tag}`).join(" ")}` : ""}
                </p>
                {publication.state === "succeeded" && publication.remote_status === "uploaded" && (
                  <p className="mt-0.5 text-xs text-muted-foreground">{t.publishing.processing}</p>
                )}
                {publication.remote_status === "sent_to_inbox" && (
                  <p className="mt-0.5 text-xs text-muted-foreground">{t.publishing.sentToInbox}</p>
                )}
                {publication.channel === "facebook" && publication.remote_status === "draft" && (
                  <p className="mt-0.5 text-xs text-muted-foreground">{t.publishing.facebookDraft}</p>
                )}
                {publication.state === "scheduled" && publication.scheduled_for && (
                  <p className="mt-0.5 text-xs text-info">{t.publishing.scheduledFor(formatDateTime(publication.scheduled_for))}</p>
                )}
                {publication.channel === "youtube" &&
                  publication.remote_privacy &&
                  publication.remote_privacy !== publication.privacy_status && (
                  <p className="mt-0.5 text-xs text-warning">
                    {t.publishing.keptAs(t.publishing.privacy[publication.remote_privacy])}
                  </p>
                )}
                {publication.last_error && (
                  <p className="mt-0.5 text-xs text-warning">
                    {t.publishing.errors[publication.last_error] ?? publication.last_error}
                  </p>
                )}
                <div className="mt-1 flex flex-wrap gap-3 text-xs">
                  {publication.youtube_url && (
                    <a
                      href={publication.youtube_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-primary hover:underline"
                    >
                      {t.publishing.openYoutube} <ExternalLink className="size-3" />
                    </a>
                  )}
                  {publication.url && publication.channel !== "youtube" && (
                    <a
                      href={publication.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-primary hover:underline"
                    >
                      {t.publishing.openPage} <ExternalLink className="size-3" />
                    </a>
                  )}
                  {publication.remote_id && publication.channel === "youtube" && (
                    <a
                      href={`https://studio.youtube.com/video/${encodeURIComponent(publication.remote_id)}/edit`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-primary hover:underline"
                    >
                      {t.publishing.openStudio} <ExternalLink className="size-3" />
                    </a>
                  )}
                  {publication.can_retry && status(publication.channel)?.status === "connected" && (
                    <button
                      type="button"
                      onClick={() => setRetrying(publication)}
                      className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
                    >
                      <RotateCcw className="size-3" /> {t.publishing.retry}
                    </button>
                  )}
                  {publication.can_reschedule && (
                    <button
                      type="button"
                      onClick={() => setScheduling({ publication, mode: "reschedule" })}
                      className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
                    >
                      <CalendarClock className="size-3" /> {t.publishing.reschedule}
                    </button>
                  )}
                  {publication.can_cancel && (
                    <button
                      type="button"
                      onClick={() => setScheduling({ publication, mode: "cancel" })}
                      className="inline-flex items-center gap-1 text-muted-foreground hover:text-destructive"
                    >
                      <XCircle className="size-3" /> {t.publishing.cancel}
                    </button>
                  )}
                </div>
              </div>
              <span className="flex items-center gap-2 text-muted-foreground">
                <PlatformIcon platform={publication.channel} />
                {platformLabel[publication.channel]}
              </span>
              <span className="text-muted-foreground">
                {formatDateTime(
                  publication.state === "scheduled" && publication.scheduled_for
                    ? publication.scheduled_for
                    : (publication.published_at ?? publication.finished_at ?? publication.created_at),
                )}
              </span>
              <StatusBadge
                status={publication.state}
                label={t.status.publication[publication.state]}
                className="justify-self-start"
              />
            </div>
          ))}
        </div>
      </section>
      {total > PAGE && (
        <div className="-mt-4 flex items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>{t.table.range(String(offset + 1), String(Math.min(offset + PAGE, total)), String(total))}</span>
          <div className="flex gap-1.5">
            <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              {t.table.previous}
            </Button>
            <Button variant="outline" size="sm" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
              {t.table.next}
            </Button>
          </div>
        </div>
      )}
      <RetryDialog publication={retrying} onClose={() => setRetrying(null)} />
      <ScheduleDialog
        publication={scheduling?.publication ?? null}
        mode={scheduling?.mode ?? "reschedule"}
        onClose={() => setScheduling(null)}
      />
    </div>
  );
}
