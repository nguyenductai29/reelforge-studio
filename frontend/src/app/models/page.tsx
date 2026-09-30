"use client";

import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Bot, Loader2, MoreHorizontal, Plus } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EmptyState, FieldLabel, FilterPills, PageHeader } from "@/components/reelforge/primitives";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { keys, useAiTools } from "@/lib/queries";
import type { AiTask, AiTool } from "@/lib/types";

const TASKS: AiTask[] = ["script", "image", "video", "voice", "music"];
const VIDEO_PROVIDERS = new Set(["fal", "runware", "replicate", "runway", "dola"]);
/** Image providers the backend can call (task "image"). */
const IMAGE_PROVIDERS = new Set(["runway"]);
/** Text-to-speech providers the backend can call (task "voice"). */
const VOICE_PROVIDERS = new Set(["gemini"]);
/** Text providers the backend can call (task "script", shown as Text). */
const TEXT_PROVIDERS = new Set(["openai", "anthropic", "gemini"]);
const presets = [
  { task: "video", provider: "fal", model: "fal-ai/veo3.1/fast", label: "fal · Veo 3.1 Fast" },
  { task: "video", provider: "runware", model: "bytedance:seedance@2.5", label: "Runware · Seedance 2.5" },
  { task: "video", provider: "replicate", model: "google/veo-3.1-fast", label: "Replicate · Veo 3.1 Fast" },
  { task: "video", provider: "runway", model: "gen4.5", label: "Runway Dev · Gen-4.5", note: "noAudio" },
  { task: "video", provider: "dola", model: "seedance-2.5", label: "Dola Gateway · Seedance 2.5", note: "experimental" },
  { task: "image", provider: "runway", model: "gen4_image", label: "Runway · Gen-4 Image" },
  { task: "voice", provider: "gemini", model: "gemini-2.5-flash-preview-tts", label: "Google · Gemini 2.5 Flash TTS" },
  { task: "script", provider: "openai", model: "gpt-4.1-mini", label: "OpenAI · GPT-4.1 mini" },
  { task: "script", provider: "anthropic", model: "claude-opus-5-5", label: "Anthropic · Claude Opus 5.5" },
  { task: "script", provider: "gemini", model: "gemini-2.5-flash", label: "Google · Gemini 2.5 Flash" },
] as const;

const isRunnable = (tool: AiTool) =>
  (tool.task === "video" && VIDEO_PROVIDERS.has(tool.provider)) ||
  (tool.task === "image" && IMAGE_PROVIDERS.has(tool.provider)) ||
  (tool.task === "voice" && VOICE_PROVIDERS.has(tool.provider)) ||
  (tool.task === "script" && TEXT_PROVIDERS.has(tool.provider));

type Draft = { id?: string; task: AiTask; provider: string; model: string; is_enabled: boolean };

function ToolDialog({ draft, onClose }: { draft: Draft | null; onClose: () => void }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const showError = useErrorToast();
  const [form, setForm] = useState<Draft | null>(draft);
  const [busy, setBusy] = useState(false);
  const d = t.models.dialog;
  if (!form) return null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    setBusy(true);
    try {
      const body = { task: form.task, provider: form.provider.trim(), model: form.model.trim(), is_enabled: form.is_enabled };
      await api(
        form.id ? `ai-tools/${encodeURIComponent(form.id)}` : "ai-tools",
        jsonRequest(form.id ? "PUT" : "POST", body),
      );
      await client.invalidateQueries({ queryKey: keys.aiTools });
      await client.invalidateQueries({ queryKey: ["readiness"] });
      toast.success(t.models.saved);
      onClose();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{form.id ? d.editTitle : d.addTitle}</DialogTitle>
          <DialogDescription>{d.description}</DialogDescription>
        </DialogHeader>
        <form id="tool-form" onSubmit={submit} className="space-y-4">
          {!form.id && (
            <div>
              <FieldLabel>{d.presets}</FieldLabel>
              <div className="flex flex-wrap gap-2">
                {presets.map((preset) => (
                  <button
                    key={preset.model}
                    type="button"
                    onClick={() => setForm({ ...form, task: preset.task, provider: preset.provider, model: preset.model })}
                    className="rounded-lg border border-border bg-surface-2 px-2.5 py-1.5 text-xs text-muted-foreground hover:text-foreground"
                  >
                    {preset.label}
                    {"note" in preset ? ` (${t.models[preset.note]})` : ""}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div>
            <FieldLabel>{d.task}</FieldLabel>
            <Select value={form.task} onValueChange={(v) => setForm({ ...form, task: v as AiTask })}>
              <SelectTrigger className="bg-surface-2" aria-label={d.task}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TASKS.map((task) => (
                  <SelectItem key={task} value={task}>
                    {t.models.categories[task]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <FieldLabel htmlFor="tool-provider">{d.provider}</FieldLabel>
            <Input
              id="tool-provider"
              required
              maxLength={60}
              pattern="[a-zA-Z0-9._\-]+"
              value={form.provider}
              placeholder={d.providerPlaceholder}
              onChange={(e) => setForm({ ...form, provider: e.target.value })}
              className="bg-surface-2"
            />
          </div>
          <div>
            <FieldLabel htmlFor="tool-model">{d.model}</FieldLabel>
            <Input
              id="tool-model"
              required
              maxLength={100}
              pattern="[a-zA-Z0-9._:\/@\-]+"
              value={form.model}
              placeholder={d.modelPlaceholder}
              onChange={(e) => setForm({ ...form, model: e.target.value })}
              className="bg-surface-2"
            />
          </div>
          <label className="flex items-center justify-between gap-3 text-sm">
            {d.enabled}
            <Switch checked={form.is_enabled} onCheckedChange={(v) => setForm({ ...form, is_enabled: v })} />
          </label>
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            {t.common.cancel}
          </Button>
          <Button type="submit" form="tool-form" disabled={busy}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {d.submit}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function ModelsPage() {
  const { t } = useI18n();
  useDocumentTitle(t.models.title);
  const client = useQueryClient();
  const showError = useErrorToast();
  const tools = useAiTools();
  const [category, setCategory] = useState<AiTask>("video");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [removing, setRemoving] = useState<AiTool | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const visible = (tools.data ?? []).filter((tool) => tool.task === category);

  async function toggle(tool: AiTool, enabled: boolean) {
    setBusy(tool.id);
    try {
      await api(
        `ai-tools/${encodeURIComponent(tool.id)}`,
        jsonRequest("PUT", { task: tool.task, provider: tool.provider, model: tool.model, is_enabled: enabled }),
      );
      await client.invalidateQueries({ queryKey: keys.aiTools });
      await client.invalidateQueries({ queryKey: ["readiness"] });
      toast.success(enabled ? t.models.enabledToast : t.models.disabledToast);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    if (!removing) return;
    setBusy(removing.id);
    try {
      await api(`ai-tools/${encodeURIComponent(removing.id)}`, { method: "DELETE" });
      await client.invalidateQueries({ queryKey: keys.aiTools });
      await client.invalidateQueries({ queryKey: ["readiness"] });
      toast.success(t.models.deleted);
      setRemoving(null);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.models.title}
        subtitle={t.models.subtitle}
        actions={
          <Button onClick={() => setDraft({ task: category, provider: "", model: "", is_enabled: true })}>
            <Plus className="size-4" /> {t.models.addModel}
          </Button>
        }
      />
      <FilterPills
        value={category}
        onChange={setCategory}
        options={TASKS.map((task) => ({ value: task, label: t.models.categories[task] }))}
      />

      {tools.isPending ? (
        <Loader2 className="mx-auto size-5 animate-spin text-muted-foreground" />
      ) : visible.length === 0 ? (
        <EmptyState
          icon={Bot}
          title={t.models.empty}
          description={t.models.emptyHint}
          action={
            <Button variant="outline" onClick={() => setDraft({ task: category, provider: "", model: "", is_enabled: true })}>
              <Plus className="size-4" /> {t.models.addModel}
            </Button>
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {visible.map((tool) => {
            const runnable = isRunnable(tool);
            return (
              <div key={tool.id} className="panel space-y-3 p-5">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="break-all font-medium">{tool.model}</p>
                    <p className="text-xs text-muted-foreground">{tool.provider}</p>
                  </div>
                  <div className="flex items-center gap-1">
                    <Switch
                      checked={tool.is_enabled}
                      disabled={busy === tool.id}
                      onCheckedChange={(v) => void toggle(tool, v)}
                      aria-label={tool.model}
                    />
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="icon" className="size-7" aria-label={t.common.edit}>
                          <MoreHorizontal className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => setDraft({ ...tool })}>{t.common.edit}</DropdownMenuItem>
                        <DropdownMenuItem className="text-destructive" onClick={() => setRemoving(tool)}>
                          {t.common.delete}
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </div>
                <p className="text-sm text-muted-foreground">{t.models.capability[tool.task]}</p>
                <div className="grid grid-cols-3 gap-2 border-t border-border pt-3 text-xs">
                  <div>
                    <p className="text-muted-foreground">{t.models.stats.execution}</p>
                    <p className={runnable ? "text-success" : undefined}>{runnable ? t.models.runnable : t.models.configOnly}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">{t.models.stats.status}</p>
                    <p>{tool.is_enabled ? t.common.enabled : t.common.disabled}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">{t.models.stats.credits}</p>
                    <p>{runnable ? (tool.task === "video" ? t.models.perClip : t.models.perGeneration) : t.common.none}</p>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {draft && <ToolDialog key={draft.id ?? "new"} draft={draft} onClose={() => setDraft(null)} />}
      <AlertDialog open={Boolean(removing)} onOpenChange={(open) => !open && setRemoving(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t.models.deleteTitle}</AlertDialogTitle>
            <AlertDialogDescription>
              {removing ? t.models.deleteDescription(`${removing.provider} / ${removing.model}`) : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                void remove();
              }}
            >
              {t.common.delete}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
