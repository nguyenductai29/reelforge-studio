"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { IntermediateCleanup, StorageMeter } from "./storage";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { keys, useAiTools, useDefaultModels, useStorage } from "@/lib/queries";
import { formatBytes } from "@/lib/studio";
import type { AiTask, DefaultModels } from "@/lib/types";
import { FieldLabel } from "./primitives";

const TASKS: { key: keyof DefaultModels; task: AiTask }[] = [
  { key: "text", task: "script" },
  { key: "image", task: "image" },
  { key: "video", task: "video" },
  { key: "voice", task: "voice" },
  { key: "transcription", task: "transcription" },
];
// Radix Select items cannot use an empty string, so "no default" gets its own token.
const NONE = "__none__";

/**
 * The workspace's default model per task: steps that choose no model use it; a model chosen on a step
 * always wins, and a disabled default falls back to the first compatible model (backend ``find_tool``).
 */
export function DefaultModelsForm({ footer }: { footer?: ReactNode }) {
  const { t } = useI18n();
  const s = t.settings.ai;
  const client = useQueryClient();
  const showError = useErrorToast();
  const tools = useAiTools().data ?? [];
  const saved = useDefaultModels().data;
  const [draft, setDraft] = useState<DefaultModels | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (saved) setDraft(saved);
  }, [saved]);

  async function save() {
    if (!draft) return;
    setBusy(true);
    try {
      await api("settings/default-models", jsonRequest("PUT", draft));
      await client.invalidateQueries({ queryKey: keys.defaultModels });
      await client.invalidateQueries({ queryKey: ["readiness"] });
      toast.success(t.settings.savedToast);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  if (!draft) return <Loader2 className="size-4 animate-spin text-muted-foreground" />;
  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm font-medium">{s.defaultModels}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">{s.defaultModelsHint}</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {TASKS.map(({ key, task }) => {
          const choices = tools.filter((tool) => tool.task === task && tool.is_enabled);
          const value = draft[key] && choices.some((tool) => tool.id === draft[key]) ? draft[key]! : NONE;
          return (
            <div key={key}>
              <FieldLabel htmlFor={`default-${key}`}>{s.tasks[key]}</FieldLabel>
              <Select value={value} onValueChange={(v) => setDraft({ ...draft, [key]: v === NONE ? null : v })}>
                <SelectTrigger id={`default-${key}`} className="bg-surface" aria-label={s.tasks[key]}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>{s.firstEnabled}</SelectItem>
                  {choices.map((tool) => (
                    <SelectItem key={tool.id} value={tool.id}>
                      {tool.provider} · {tool.model}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          );
        })}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button size="sm" disabled={busy} onClick={() => void save()}>
          {busy && <Loader2 className="size-3.5 animate-spin" />}
          {s.saveDefaults}
        </Button>
        {footer}
      </div>
    </div>
  );
}

/** Stored media against the workspace quota, by kind; ``lead`` opens the usage column, ``aside`` ends the other. */
export function StorageUsagePanel({ lead, aside }: { lead?: ReactNode; aside?: ReactNode }) {
  const { t } = useI18n();
  const s = t.settings.storage;
  const usage = useStorage().data;
  if (!usage) return <Loader2 className="size-4 animate-spin text-muted-foreground" />;
  return (
    // Usage beside the cleanup on wide screens, so the Storage tab fits a laptop window without scrolling.
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="space-y-3">
        {lead}
        <StorageMeter info={usage} />
        <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
          {(["video", "audio", "image", "document"] as const).map((kind) => (
            <div key={kind} className="rounded-lg bg-surface-2 p-2">
              <p className="text-muted-foreground">{s.byType[kind]}</p>
              <p className="font-medium">{formatBytes(usage.by_type[kind])}</p>
            </div>
          ))}
        </div>
      </div>
      <div className="space-y-3">
        {usage.retention && (
          <p className="text-xs text-muted-foreground">
            {t.storage.retention(usage.retention.intermediate_days, usage.retention.temp_days)}
          </p>
        )}
        {usage.intermediate && <IntermediateCleanup />}
        <p className="text-xs text-muted-foreground">{s.cleanupHint}</p>
        {aside}
      </div>
    </div>
  );
}
