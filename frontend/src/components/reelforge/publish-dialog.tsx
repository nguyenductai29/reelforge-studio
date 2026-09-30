"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
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
import type { Run } from "@/lib/types";
import { FieldLabel } from "./primitives";

/** Lists approved, not yet published clips and queues a private YouTube upload. */
export function PublishDialog({ trigger }: { trigger: ReactNode }) {
  const { t, formatRelative } = useI18n();
  const client = useQueryClient();
  const showError = useErrorToast();
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
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
  const selected = approved.find((asset) => asset.id === choice) ?? approved[0];
  const projectTitle = (projectId: string | null) => data?.projects.find((p) => p.id === projectId)?.title ?? "";

  useEffect(() => {
    if (selected) setTitle(projectTitle(selected.project_id).slice(0, 100));
    // Prefill the title only when the chosen clip changes.
  }, [selected?.id]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!selected?.run_id) return;
    setBusy(true);
    try {
      await api(
        "youtube/publications",
        jsonRequest("POST", { run_id: selected.run_id, asset_id: selected.id, title: title.trim(), description }),
      );
      await client.invalidateQueries({ queryKey: keys.publications });
      toast.success(d.queued);
      setOpen(false);
      setDescription("");
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
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
          </form>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            {t.common.cancel}
          </Button>
          <Button type="submit" form="publish-form" disabled={busy || !selected || !title.trim() || !connection.data?.connected}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {d.submit}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
