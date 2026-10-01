"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { keys, useChannels, useDashboard, usePublications, useRuns } from "@/lib/queries";
import { approvedAsset, runVideos } from "@/lib/studio";
import type { ChannelId, PrivacyStatus, PublishMetadata, Run, RunSummary } from "@/lib/types";
import { cn } from "@/lib/utils";
import { FieldLabel, PlatformIcon, platformLabel } from "./primitives";

const CHANNELS: ChannelId[] = ["youtube", "tiktok", "facebook"];
/** Visibility each channel accepts: TikTok inbox drafts are private; Facebook is a published Reel or a draft. */
const PRIVACY: Record<ChannelId, PrivacyStatus[]> = {
  youtube: ["private", "unlisted", "public"],
  tiktok: ["private"],
  facebook: ["private", "public"],
};
/** Caption limits with hashtags, in UTF-16 units (JavaScript string length), as the backend counts them. */
const CAPTION_LIMIT: Record<"tiktok" | "facebook", number> = { tiktok: 2200, facebook: 5000 };

/** YouTube counts the tag limit on tags joined by commas, quoting tags that contain spaces. */
export const tagsLength = (tags: string[]) =>
  tags.reduce((sum, tag) => sum + tag.length + (tag.includes(" ") ? 2 : 0), 0) + Math.max(tags.length - 1, 0);
export const splitTags = (value: string) =>
  value
    .split(",")
    .map((tag) => tag.trim().replace(/^#/, ""))
    .filter(Boolean);
const utf8Length = (value: string) => new TextEncoder().encode(value).length;
/** TikTok and Facebook hashtags are single words, as the backend stores them. */
const hashtags = (tags: string[]) => [...new Set(tags.map((tag) => tag.replace(/[\s,#<>]+/g, "")).filter(Boolean))];
const caption = (description: string, tags: string[]) =>
  [description.trim(), hashtags(tags).map((tag) => `#${tag}`).join(" ")].filter(Boolean).join("\n\n");

type Form = { title: string; description: string; tags: string; privacy: PrivacyStatus };

function formFrom(meta: Partial<PublishMetadata> | undefined, fallbackTitle: string, channel: ChannelId): Form {
  const privacy = meta?.privacy_status && PRIVACY[channel].includes(meta.privacy_status) ? meta.privacy_status : "private";
  return {
    title: (meta?.title || fallbackTitle).slice(0, 100),
    description: meta?.description ?? "",
    tags: (meta?.tags ?? []).join(", "),
    privacy,
  };
}

/** A `datetime-local` value (the device's time) as UTC ISO, or null when empty or invalid. */
const toUtc = (local: string) => {
  if (!local) return null;
  const date = new Date(local);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
};

/**
 * Reviews and queues the upload of an approved video (the final render when the run has one) to one or
 * more channels, now or at a scheduled time. Each channel gets its own publication and upload job.
 * Opened with a run, it starts from that run's prepared metadata per channel.
 */
export function PublishDialog({
  trigger,
  runId,
  open: controlledOpen,
  onOpenChange,
}: {
  trigger?: ReactNode;
  runId?: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const { t, formatRelative } = useI18n();
  const client = useQueryClient();
  const showError = useErrorToast();
  const [ownOpen, setOwnOpen] = useState(false);
  const open = controlledOpen ?? ownOpen;
  const setOpen = onOpenChange ?? setOwnOpen;
  const [choice, setChoice] = useState("");
  const [chosen, setChosen] = useState<ChannelId[]>([]);
  const [forms, setForms] = useState<Partial<Record<ChannelId, Form>>>({});
  const [tab, setTab] = useState<ChannelId>("youtube");
  const [when, setWhen] = useState<"now" | "later">("now");
  const [schedule, setSchedule] = useState("");
  const [busy, setBusy] = useState(false);
  const { data } = useDashboard();
  const runs = useRuns().data ?? [];
  const publications = usePublications().data ?? [];
  const channels = useChannels();
  const d = t.publishing.dialog;

  const connected = new Set((channels.data ?? []).filter((c) => c.status === "connected").map((c) => c.channel));
  // Only finished runs can hold an approved clip; fetch their steps to confirm the approval.
  const candidates = runVideos(data?.assets ?? []).filter((asset) => {
    const run = runs.find((r) => r.id === asset.run_id);
    return run && ["completed", "blocked"].includes(run.status);
  });
  const details = useQueries({
    queries: candidates.map((asset) => ({
      queryKey: keys.run(asset.run_id!),
      queryFn: () => api<Run>(`workflow-runs/${encodeURIComponent(asset.run_id!)}`),
      enabled: open,
    })),
  });
  const loading = open && (details.some((q) => q.isPending) || channels.isPending);
  const approved = candidates.filter((asset, i) => approvedAsset(details[i]?.data) === asset.id);
  const selected =
    approved.find((asset) => asset.id === choice) ?? approved.find((asset) => asset.run_id === runId) ?? approved[0];
  // A run is published to each channel at most once (a cancelled one can be published again).
  const taken = new Set(
    publications.filter((p) => p.run_id === selected?.run_id && p.state !== "cancelled").map((p) => p.channel),
  );
  const available = CHANNELS.filter((channel) => connected.has(channel) && !taken.has(channel));
  const summary = useQuery({
    queryKey: keys.runSummary(selected?.run_id ?? ""),
    queryFn: () => api<RunSummary>(`workflow-runs/${encodeURIComponent(selected!.run_id!)}/summary`),
    enabled: open && Boolean(selected?.run_id),
  });
  const projectTitle = (projectId: string | null) => data?.projects.find((p) => p.id === projectId)?.title ?? "";

  // Start from the run's prepared metadata per channel whenever the chosen video changes.
  const defaults = summary.data?.publishing.defaults;
  useEffect(() => {
    if (!selected) return;
    const fallback = projectTitle(selected.project_id);
    const next: Partial<Record<ChannelId, Form>> = {};
    for (const channel of CHANNELS) {
      next[channel] = formFrom(defaults?.platforms?.[channel] ?? (channel === "youtube" ? defaults : undefined), fallback, channel);
    }
    setForms(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id, JSON.stringify(defaults ?? null)]);
  useEffect(() => {
    setChosen((current) => {
      const kept = current.filter((channel) => available.includes(channel));
      return kept.length ? kept : available.slice(0, 1);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [available.join("|")]);
  useEffect(() => {
    if (chosen.length && !chosen.includes(tab)) setTab(chosen[0]!);
  }, [chosen, tab]);

  const scheduledFor = when === "later" ? toUtc(schedule) : null;
  const problems: string[] = [];
  for (const channel of chosen) {
    const form = forms[channel];
    if (!form) continue;
    const tags = splitTags(form.tags);
    const name = platformLabel[channel];
    if (!form.title.trim()) problems.push(`${name}: ${t.config.errors.invalid_title}`);
    if (channel === "youtube") {
      if (utf8Length(form.description) > 5000) problems.push(d.descriptionTooLong);
      if (tagsLength(tags) > 500) problems.push(d.tagsTooLong);
      if (/[<>]/.test(form.title + form.description + form.tags)) problems.push(d.noAngles);
    } else {
      if (hashtags(tags).length > 30) problems.push(d.tooManyTags(name));
      if (caption(form.description, tags).length > CAPTION_LIMIT[channel]) {
        problems.push(d.captionTooLong(name, CAPTION_LIMIT[channel]));
      }
    }
  }
  if (when === "later" && (!scheduledFor || new Date(scheduledFor).getTime() <= Date.now())) problems.push(d.pastSchedule);

  function update(channel: ChannelId, change: Partial<Form>) {
    setForms((current) => ({ ...current, [channel]: { ...current[channel]!, ...change } }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!selected?.run_id || !chosen.length || problems.length) return;
    setBusy(true);
    try {
      const targets = chosen.map((channel) => {
        const form = forms[channel]!;
        return {
          channel,
          title: form.title.trim(),
          description: form.description,
          tags: channel === "youtube" ? splitTags(form.tags) : hashtags(splitTags(form.tags)),
          privacy_status: form.privacy,
        };
      });
      await api(
        "publications",
        jsonRequest("POST", { run_id: selected.run_id, asset_id: selected.id, scheduled_for: scheduledFor, targets }),
      );
      await client.invalidateQueries({ queryKey: keys.publications });
      await client.invalidateQueries({ queryKey: keys.runSummary(selected.run_id) });
      toast.success(scheduledFor ? d.scheduledMany(targets.length) : d.queuedMany(targets.length));
      setOpen(false);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  const noChannel = channels.data && connected.size === 0;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      {trigger && <DialogTrigger asChild>{trigger}</DialogTrigger>}
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{d.multiTitle}</DialogTitle>
          <DialogDescription>{d.multiDescription}</DialogDescription>
        </DialogHeader>
        {noChannel ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-surface p-3 text-sm text-muted-foreground">
            {d.needConnect}
            <Button asChild size="sm" variant="outline">
              <Link href="/channels">{t.publishing.connect}</Link>
            </Button>
          </div>
        ) : loading ? (
          <Loader2 className="mx-auto my-6 size-5 animate-spin text-muted-foreground" />
        ) : approved.length === 0 ? (
          <p className="rounded-lg border border-border bg-surface p-3 text-sm text-muted-foreground">{d.noVideos}</p>
        ) : (
          <form id="publish-form" onSubmit={submit} className="space-y-4">
            <div>
              <FieldLabel>{d.video}</FieldLabel>
              <Select value={selected?.id} onValueChange={setChoice}>
                <SelectTrigger className="bg-surface" aria-label={d.video}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {approved.map((asset) => (
                    <SelectItem key={asset.id} value={asset.id}>
                      {projectTitle(asset.project_id) || asset.filename}
                      {asset.created_at ? ` · ${formatRelative(asset.created_at)}` : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {summary.data?.final_video && (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {summary.data.final_video.final ? d.finalRender : d.clipOnly}
                </p>
              )}
            </div>

            <div>
              <FieldLabel>{d.channels}</FieldLabel>
              <div className="flex flex-wrap gap-2">
                {CHANNELS.map((channel) => {
                  const on = chosen.includes(channel);
                  const disabled = !available.includes(channel);
                  return (
                    <button
                      key={channel}
                      type="button"
                      disabled={disabled}
                      aria-pressed={on}
                      onClick={() => setChosen(on ? chosen.filter((c) => c !== channel) : [...chosen, channel])}
                      className={cn(
                        "flex items-center gap-2 rounded-lg border px-3 py-2 text-sm",
                        on ? "border-primary bg-primary/10" : "border-border bg-surface",
                        disabled && "cursor-not-allowed opacity-50",
                      )}
                    >
                      <PlatformIcon platform={channel} />
                      {platformLabel[channel]}
                      {disabled && (
                        <span className="text-[11px] text-muted-foreground">
                          · {taken.has(channel) ? d.alreadyOn : d.notConnected}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>

            {chosen.length > 0 && (
              <Tabs value={tab} onValueChange={(value) => setTab(value as ChannelId)}>
                <TabsList>
                  {chosen.map((channel) => (
                    <TabsTrigger key={channel} value={channel}>
                      {platformLabel[channel]}
                    </TabsTrigger>
                  ))}
                </TabsList>
                {chosen.map((channel) => {
                  const form = forms[channel];
                  if (!form) return null;
                  const tags = splitTags(form.tags);
                  const id = (field: string) => `publish-${channel}-${field}`;
                  return (
                    <TabsContent key={channel} value={channel} className="mt-4 space-y-4">
                      <div>
                        <FieldLabel htmlFor={id("title")}>{d.titleLabel}</FieldLabel>
                        <Input
                          id={id("title")}
                          value={form.title}
                          maxLength={100}
                          onChange={(e) => update(channel, { title: e.target.value.replace(/[\r\n]/g, " ") })}
                          className="bg-surface"
                        />
                      </div>
                      <div>
                        <FieldLabel htmlFor={id("description")}>
                          {channel === "tiktok" ? d.captionLabel : d.descriptionLabel}
                        </FieldLabel>
                        <Textarea
                          id={id("description")}
                          rows={4}
                          maxLength={5000}
                          value={form.description}
                          onChange={(e) => update(channel, { description: e.target.value })}
                          className="resize-none bg-surface"
                        />
                        {channel === "tiktok" && <p className="mt-1 text-[11px] text-muted-foreground">{d.tiktokDraftHint}</p>}
                      </div>
                      <div>
                        <FieldLabel htmlFor={id("tags")}>{d.tagsLabel}</FieldLabel>
                        <Input
                          id={id("tags")}
                          value={form.tags}
                          placeholder={d.tagsPlaceholder}
                          onChange={(e) => update(channel, { tags: e.target.value })}
                          className="bg-surface"
                        />
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          {channel === "youtube" ? d.tagsHint(tagsLength(tags)) : d.hashtagsHint(hashtags(tags).length)}
                        </p>
                      </div>
                      {PRIVACY[channel].length > 1 && (
                        <div>
                          <FieldLabel>{d.privacyLabel}</FieldLabel>
                          <Select
                            value={form.privacy}
                            onValueChange={(value) => update(channel, { privacy: value as PrivacyStatus })}
                          >
                            <SelectTrigger className="bg-surface" aria-label={d.privacyLabel}>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              {PRIVACY[channel].map((value) => (
                                <SelectItem key={value} value={value}>
                                  {channel === "facebook"
                                    ? d.facebookPrivacy[value as "public" | "private"]
                                    : t.publishing.privacy[value]}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          {channel === "youtube" && <p className="mt-1 text-[11px] text-muted-foreground">{d.privacyHint}</p>}
                        </div>
                      )}
                    </TabsContent>
                  );
                })}
              </Tabs>
            )}

            <div>
              <FieldLabel>{d.when}</FieldLabel>
              <div className="flex flex-wrap items-center gap-2">
                {(["now", "later"] as const).map((value) => (
                  <Button
                    key={value}
                    type="button"
                    size="sm"
                    variant={when === value ? "default" : "outline"}
                    onClick={() => setWhen(value)}
                  >
                    {d[value]}
                  </Button>
                ))}
                {when === "later" && (
                  <Input
                    type="datetime-local"
                    aria-label={d.later}
                    value={schedule}
                    onChange={(e) => setSchedule(e.target.value)}
                    className="w-auto bg-surface"
                  />
                )}
              </div>
              {when === "later" && <p className="mt-1 text-[11px] text-muted-foreground">{d.scheduleHint}</p>}
            </div>

            {chosen.length === 0 && <p className="text-xs text-muted-foreground">{d.chooseChannel}</p>}
            {problems.length > 0 && (
              <ul className="space-y-1 text-xs text-destructive">
                {problems.map((problem) => (
                  <li key={problem}>{problem}</li>
                ))}
              </ul>
            )}
          </form>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            {t.common.cancel}
          </Button>
          <Button
            type="submit"
            form="publish-form"
            disabled={busy || !selected || !chosen.length || problems.length > 0 || Boolean(noChannel)}
          >
            {busy && <Loader2 className="size-4 animate-spin" />}
            {when === "later" ? d.submitScheduled : d.submitMany}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
