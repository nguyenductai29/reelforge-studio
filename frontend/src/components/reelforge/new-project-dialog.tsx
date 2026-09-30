"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
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
import { Textarea } from "@/components/ui/textarea";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { keys } from "@/lib/queries";
import type { Project } from "@/lib/types";
import { FieldLabel } from "./primitives";

export function NewProjectDialog({ trigger, onCreated }: { trigger: ReactNode; onCreated?: (project: Project) => void }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const showError = useErrorToast();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    setBusy(true);
    try {
      const project = await api<Project>(
        "projects",
        jsonRequest("POST", { title: String(fields.get("title") ?? "").trim(), topic: String(fields.get("topic") ?? "") }),
      );
      await client.invalidateQueries({ queryKey: keys.dashboard });
      toast.success(t.projects.created);
      setOpen(false);
      onCreated?.(project);
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
          <DialogTitle>{t.projects.dialog.title}</DialogTitle>
          <DialogDescription>{t.projects.dialog.description}</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <FieldLabel htmlFor="project-title">{t.projects.dialog.name}</FieldLabel>
            <Input
              id="project-title"
              name="title"
              maxLength={150}
              required
              placeholder={t.projects.dialog.namePlaceholder}
              className="bg-surface-2"
            />
          </div>
          <div>
            <FieldLabel htmlFor="project-topic">{t.projects.dialog.topic}</FieldLabel>
            <Textarea
              id="project-topic"
              name="topic"
              rows={4}
              maxLength={3000}
              placeholder={t.projects.dialog.topicPlaceholder}
              className="resize-none bg-surface-2"
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              {t.common.cancel}
            </Button>
            <Button type="submit" disabled={busy}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              {t.projects.dialog.submit}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
