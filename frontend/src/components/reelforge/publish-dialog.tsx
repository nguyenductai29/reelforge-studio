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
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { keys, useDashboard, usePublications, useRuns, useYouTubeConnection } from "@/lib/queries";
import { approvedAsset, runVideos } from "@/lib/studio";
import type { PrivacyStatus, Run, RunSummary } from "@/lib/types";
import { FieldLabel } from "./primitives";

const PRIVACY: PrivacyStatus[] = ["private", "unlisted", "public"];
/** YouTube counts the tag limit on tags joined by commas, quoting tags that contain spaces. */
export const tagsLength = (tags: string[]) =>
  tags.reduce((sum, tag) => sum + tag.length + (tag.includes(" ") ? 2 : 0), 0) + Math.max(tags.length - 1, 0);
export const splitTags = (value: string) =>
  value
    .split(",")
    .map((tag) => tag.trim().replace(/^#/, ""))
    .filter(Boolean);
const utf8Length = (value: string) => new TextEncoder().encode(value).length;

/**
 * Reviews and queues a YouTube upload of an approved video: the final render when the run has one.
 * Opened with a run, it starts from that run's prepared metadata (Publish step, Metadata step or project title).
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
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [privacy, setPrivacy] = useState<PrivacyStatus>("private");
  const [busy, setBusy] = useState(false);
  const { data } = useDashboard();
  const runs = useRuns().data ?? [];
  const publications = usePublications().data ?? [];
  const connection = useYouTubeConnection();
  const d = t.publishing.dialog;

  const published = new Set(publications.map((p) => p.run_id));
  // Only finished runs can hold an approved clip; fetch their steps to confirm the approval.
  const candidates = runVideos(data?.assets ?? []).filter((asset) => {
    const run = runs.find((r) => r.id === asset.run_id);
    return run && ["completed", "blocked"].includes(run.status) && !published.has(run.id);
  });
  const details = useQueries({
    queries: candidates.map((asset) => ({
      queryKey: keys.run(asset.run_id!),
      queryFn: () => api<Run>(`workflow-runs/${encodeURIComponent(asset.run_id!)}`),
      enabled: open,
    })),
  });
  const loading = open && details.some((q) => q.isPending);
  const approved = candidates.filter((asset, i) => approvedAsset(details[i]?.data) === asset.id);
  const selected =
    approved.find((asset) => asset.id === choice) ?? approved.find((asset) => asset.run_id === runId) ?? approved[0];
  const summary = useQuery({
    queryKey: keys.runSummary(selected?.run_id ?? ""),
    queryFn: () => api<RunSummary>(`workflow-runs/${encodeURIComponent(selected!.run_id!)}/summary`),
    enabled: open && Boolean(selected?.run_id),
  });
  const projectTitle = (projectId: string | null) => data?.projects.find((p) => p.id === projectId)?.title ?? "";
  const tagList = splitTags(tags);
  const problems = [
    utf8Length(description) > 5000 ? d.descriptionTooLong : null,
    tagsLength(tagList) > 500 ? d.tagsTooLong : null,
    /[<>]/.test(title + description + tags) ? d.noAngles : null,
  ].filter(Boolean);

  // Start from the run's prepared metadata whenever the chosen video changes.
  const defaults = summary.data?.publishing.defaults;
  useEffect(() => {
    if (!selected) return;
    setTitle((defaults?.title || projectTitle(selected.project_id)).slice(0, 100));
    setDescription(defaults?.description ?? "");
    setTags((defaults?.tags ?? []).join(", "));
    setPrivacy(defaults?.privacy_status ?? "private");
  }, [selected?.id, defaults?.title, defaults?.description, defaults?.privacy_status, (defaults?.tags ?? []).join("|")]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!selected?.run_id || problems.length) return;
    setBusy(true);
    try {
      await api(
        "youtube/publications",
        jsonRequest("POST", {
          run_id: selected.run_id,
          asset_id: selected.id,
          title: title.trim(),
          description,
          tags: tagList,
          privacy_status: privacy,
        }),
      );
      await client.invalidateQueries({ queryKey: keys.publications });
      await client.invalidateQueries({ queryKey: keys.runSummary(selected.run_id) });
      toast.success(d.queued);
      setOpen(false);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      {trigger && <DialogTrigger asChild>{trigger}</DialogTrigger>}
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{d.title}</DialogTitle>
          <DialogDescription>{d.description}</DialogDescription>
        </DialogHeader>
        {connection.data && !connection.data.connected ? (
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
              <FieldLabel htmlFor="yt-title">{d.titleLabel}</FieldLabel>
              <Input
                id="yt-title"
                value={title}
                maxLength={100}
                required
                onChange={(e) => setTitle(e.target.value.replace(/[\r\n]/g, " "))}
                className="bg-surface"
              />
            </div>
            <div>
              <FieldLabel htmlFor="yt-description">{d.descriptionLabel}</FieldLabel>
              <Textarea
                id="yt-description"
                rows={4}
                maxLength={5000}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className="resize-none bg-surface"
              />
            </div>
            <div>
              <FieldLabel htmlFor="yt-tags">{d.tagsLabel}</FieldLabel>
              <Input
                id="yt-tags"
                value={tags}
                placeholder={d.tagsPlaceholder}
                onChange={(e) => setTags(e.target.value)}
                className="bg-surface"
              />
              <p className="mt-1 text-[11px] text-muted-foreground">{d.tagsHint(tagsLength(tagList))}</p>
            </div>
            <div>
              <FieldLabel>{d.privacyLabel}</FieldLabel>
              <Select value={privacy} onValueChange={(value) => setPrivacy(value as PrivacyStatus)}>
                <SelectTrigger className="bg-surface" aria-label={d.privacyLabel}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PRIVACY.map((value) => (
                    <SelectItem key={value} value={value}>
                      {t.publishing.privacy[value]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="mt-1 text-[11px] text-muted-foreground">{d.privacyHint}</p>
            </div>
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
            disabled={busy || !selected || !title.trim() || problems.length > 0 || !connection.data?.connected}
          >
            {busy && <Loader2 className="size-4 animate-spin" />}
            {d.submit}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
