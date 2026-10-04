"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Eraser, Loader2, RotateCcw, ShieldCheck, Undo2 } from "lucide-react";
import { toast } from "sonner";
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
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { ApiError, api, jsonRequest } from "@/lib/api";
import { errorText, useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import type { Dictionary } from "@/lib/i18n/vi";
import { keys, useBackups, useSettings, useSystemConfig } from "@/lib/queries";
import { GIB, formatBytes } from "@/lib/studio";
import type { DriveTestResult, ProviderTest, SecretUpdate, SystemConfigOverview, SystemSection, SystemSetting } from "@/lib/types";
import { AdminMovieSources } from "./admin-movie-sources";
import { cn } from "@/lib/utils";

const NAV = ["security", "general", "email", "ai", "social", "storage", "backups", "movie_sources", "runtime", "credits", "notifications"] as const;
type Nav = (typeof NAV)[number];
const AI_PROVIDERS = ["openai", "anthropic", "gemini", "runway", "fal", "runware", "replicate"] as const;
const CHANNELS = ["youtube", "tiktok", "facebook"] as const;
const STATUS_TONE: Record<string, string> = { ok: "text-success", warning: "text-warning", error: "text-destructive" };
type Labels = Dictionary["admin"]["system"]["labels"];

function SourceBadge({ setting }: { setting: SystemSetting }) {
  const { t } = useI18n();
  const s = t.admin.system;
  return (
    <span className={cn("rounded px-1.5 py-0.5 text-[10px]", setting.source === "admin" ? "bg-primary/15 text-primary"
      : setting.source === "error" ? "bg-destructive/15 text-destructive" : "bg-surface-2 text-muted-foreground")}
          title={setting.env ?? undefined}>
      {setting.source === "environment" && setting.env ? s.sourceEnv(setting.env) : s.sources[setting.source]}
    </span>
  );
}

/** Edits a group of settings and saves only what changed; secrets are write-only (blank keeps the saved value). */
function SettingsCard({ title, description, section, settings, extra, footer, labels, transform }: {
  title: string;
  description?: ReactNode;
  section: SystemSection;
  settings: SystemSetting[];
  extra?: ReactNode;
  footer?: ReactNode;
  labels: Labels;
  /** Display ↔ stored conversion for one setting (e.g. bytes shown as GB). */
  transform?: Partial<Record<string, { show: (value: number) => string; store: (text: string) => number; unit: string }>>;
}) {
  const { t, formatDateTime } = useI18n();
  const s = t.admin.system;
  const client = useQueryClient();
  const initial = (setting: SystemSetting) => {
    if (setting.kind === "secret" || setting.value === null || setting.value === undefined) return "";
    const custom = transform?.[setting.key];
    return custom && typeof setting.value === "number" ? custom.show(setting.value) : String(setting.value);
  };
  const [text, setText] = useState<Record<string, string>>(() => Object.fromEntries(settings.map((x) => [x.key, initial(x)])));
  const [flags, setFlags] = useState<Record<string, boolean>>(
    () => Object.fromEntries(settings.filter((x) => x.kind === "bool").map((x) => [x.key, Boolean(x.value)])));
  const [secrets, setSecrets] = useState<Record<string, SecretUpdate>>({});
  const [reset, setReset] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<{ files: number } | null>(null);
  const label = (key: string) => labels[key as keyof Labels] ?? key;

  function changes() {
    const values: Record<string, unknown> = {};
    for (const setting of settings) {
      if (reset.includes(setting.key) || setting.kind === "secret") continue;
      if (setting.kind === "bool") {
        if (flags[setting.key] !== Boolean(setting.value)) values[setting.key] = flags[setting.key];
        continue;
      }
      const raw = (text[setting.key] ?? "").trim();
      if (raw === initial(setting)) continue;
      if (setting.kind === "str") values[setting.key] = raw;
      else if (raw === "") continue;  // a blank number changes nothing; the reset button returns to env/default
      else {
        const custom = transform?.[setting.key];
        values[setting.key] = custom ? custom.store(raw) : Number(raw);
      }
    }
    const secretUpdates = Object.fromEntries(Object.entries(secrets).filter(([, update]) => update.action !== "keep"));
    return { values, secrets: secretUpdates, reset };
  }

  async function send(confirmRootChange = false) {
    setBusy(true);
    setFailure(null);
    try {
      await api(`admin/system-config/${section}`, jsonRequest("PUT", { ...changes(), confirm_root_change: confirmRootChange }));
      await client.invalidateQueries({ queryKey: keys.systemConfig });
      toast.success(s.saved);
    } catch (error) {
      if (error instanceof ApiError && error.error?.code === "root_change_requires_confirmation") {
        setConfirming({ files: 0 });
      } else if (error instanceof ApiError && error.error) {
        const message = s.errors[error.error.code as keyof typeof s.errors] ?? s.errors.invalid_value;
        setFailure(error.error.field ? `${message} — ${label(error.error.field)}` : message);
      } else {
        setFailure(errorText(error, t));
      }
    } finally {
      setBusy(false);
    }
  }

  const dirty = (() => {
    const pending = changes();
    return Object.keys(pending.values).length + Object.keys(pending.secrets).length + pending.reset.length > 0;
  })();

  return (
    <form className="panel space-y-3 p-4" autoComplete="off" onSubmit={(event: FormEvent) => { event.preventDefault(); void send(); }}>
      <div>
        <h3 className="text-sm font-semibold">{title}</h3>
        {description && <div className="text-xs text-muted-foreground">{description}</div>}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {settings.map((setting) => {
          const id = `setting-${setting.key}`;
          const resetting = reset.includes(setting.key);
          const header = (
            <label htmlFor={id} className="flex items-center justify-between gap-2 text-xs font-medium">
              <span>{label(setting.key)}</span>
              <span className="flex items-center gap-1">
                <SourceBadge setting={setting} />
                {setting.source === "admin" && setting.kind !== "secret" && (
                  <button type="button" title={resetting ? s.undoReset : s.reset} aria-label={resetting ? s.undoReset : s.reset}
                          className="text-muted-foreground hover:text-foreground"
                          onClick={() => setReset((current) => resetting ? current.filter((k) => k !== setting.key)
                            : [...current, setting.key])}>
                    {resetting ? <Undo2 className="size-3" /> : <RotateCcw className="size-3" />}
                  </button>
                )}
              </span>
            </label>
          );
          if (setting.kind === "bool") {
            return (
              <div key={setting.key} className="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2 sm:col-span-2">
                {header}
                <Switch id={id} checked={flags[setting.key] ?? false} disabled={resetting}
                        onCheckedChange={(on) => setFlags((current) => ({ ...current, [setting.key]: on }))} />
              </div>
            );
          }
          if (setting.kind === "secret") {
            const update = secrets[setting.key] ?? { action: "keep" };
            const cleared = update.action === "clear";
            return (
              <div key={setting.key} className="space-y-1">
                {header}
                <div className="flex items-center gap-1.5">
                  <Input id={id} type="password" autoComplete="new-password" spellCheck={false} maxLength={4096}
                         disabled={cleared} value={update.action === "replace" ? update.value : ""}
                         onChange={(e) => setSecrets((current) => ({ ...current, [setting.key]: e.target.value
                           ? { action: "replace", value: e.target.value } : { action: "keep" } }))}
                         placeholder={cleared ? s.willClear : setting.source === "error" ? s.unreadable
                           : setting.configured ? s.savedSecret : s.notSet}
                         className="h-8 bg-surface-2 font-mono text-xs" />
                  {setting.source === "admin" && (
                    <Button type="button" variant="ghost" size="icon" className="size-8 shrink-0"
                            aria-label={cleared ? s.undoReset : s.clearSecret} title={cleared ? s.undoReset : s.clearSecret}
                            onClick={() => setSecrets((current) => ({ ...current, [setting.key]: cleared ? { action: "keep" }
                              : { action: "clear" } }))}>
                      {cleared ? <Undo2 className="size-3.5" /> : <Eraser className="size-3.5" />}
                    </Button>
                  )}
                </div>
              </div>
            );
          }
          const custom = transform?.[setting.key];
          const numeric = setting.kind === "int" || setting.kind === "float";
          return (
            <div key={setting.key} className={cn("space-y-1", !numeric && "sm:col-span-2")}>
              {header}
              <div className="flex items-center gap-1.5">
                <Input id={id} value={text[setting.key] ?? ""} disabled={resetting}
                       type={numeric ? "number" : "text"} step={setting.kind === "float" || custom ? "any" : 1}
                       min={custom || setting.minimum === null ? undefined : setting.minimum}
                       max={custom || setting.maximum === null ? undefined : setting.maximum}
                       placeholder={setting.default !== null && setting.default !== "" ? s.defaultValue(
                         custom && typeof setting.default === "number" ? custom.show(setting.default) : String(setting.default)) : ""}
                       onChange={(e) => setText((current) => ({ ...current, [setting.key]: e.target.value }))}
                       className={cn("h-8 bg-surface-2 text-xs", !numeric && "font-mono")} />
                {custom && <span className="text-xs text-muted-foreground">{custom.unit}</span>}
              </div>
              {numeric && setting.minimum !== null && setting.maximum !== null && !custom && (
                <p className="text-[10px] text-muted-foreground">{s.range(setting.minimum, setting.maximum)}</p>
              )}
            </div>
          );
        })}
      </div>
      {extra}
      {failure && <p className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">{failure}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" size="sm" disabled={busy || !dirty}>
          {busy && <Loader2 className="size-3.5 animate-spin" />}
          {s.save}
        </Button>
        {footer}
        {settings.some((x) => x.updated_at) && (
          <span className="text-[11px] text-muted-foreground">
            {s.updated(formatDateTime(settings.map((x) => x.updated_at).filter(Boolean).sort().at(-1)!),
                       settings.find((x) => x.updated_by)?.updated_by ?? "—")}
          </span>
        )}
      </div>
      <AlertDialog open={confirming !== null} onOpenChange={(open) => !open && setConfirming(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{s.rootChangeTitle}</AlertDialogTitle>
            <AlertDialogDescription>{s.rootChangeDescription}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction onClick={() => { setConfirming(null); void send(true); }}>{s.rootChangeConfirm}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </form>
  );
}

/** Credentials, the root folder, a tiny upload and its permanent deletion: nothing stays in Drive. */
function DriveTestButton() {
  const { t } = useI18n();
  const v = t.admin.system.movie;
  const client = useQueryClient();
  const showError = useErrorToast();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DriveTestResult | null>(null);

  async function run() {
    setBusy(true);
    try {
      setResult(await api<DriveTestResult>("admin/system-config/movie_sources/drive/test", { method: "POST" }));
      await client.invalidateQueries({ queryKey: keys.systemConfig });
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void run()}>
        {busy ? <Loader2 className="size-3.5 animate-spin" /> : <ShieldCheck className="size-3.5" />}
        {v.test}
      </Button>
      {result && (
        <ul className="flex flex-wrap gap-x-3 gap-y-1 text-[11px]" aria-label={v.test}>
          {result.checks.map((check) => (
            <li key={check.key} className={STATUS_TONE[check.status] ?? "text-muted-foreground"}>
              {v.checks[check.key as keyof typeof v.checks] ?? check.key}: {t.admin.system.testStatus[check.status]}
              {check.code ? ` (${v.codes[check.code as keyof typeof v.codes] ?? check.code})` : ""}
              {check.account ? ` · ${check.account}` : ""}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Movie sources: Google Drive (write-only credentials and a connection test), retention, limits, folders. */
function MovieSourcesPanel({ data, version }: { data: SystemConfigOverview; version: string }) {
  const { t, formatDateTime, formatNumber } = useI18n();
  const s = t.admin.system;
  const v = s.movie;
  const settings = data.sections.movie_sources ?? [];
  const group = (name: string) => settings.filter((x) => x.group === name);
  const gigabytes = { show: (value: number) => String(Math.round((value / GIB) * 100) / 100),
                      store: (text: string) => Math.round(Number(text) * GIB), unit: "GB" };
  const info = data.movie_sources;
  const summary = info?.summary;
  const lastTest = info?.last_test;
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">{v.hint}</p>
      <div className="panel space-y-1.5 p-4 text-xs">
        <p className={info?.drive_problem ? "text-warning" : "text-success"}>
          {info?.drive_problem ? v.problems[info.drive_problem as keyof typeof v.problems] ?? info.drive_problem : v.driveReady}
        </p>
        {summary && (
          <p className="text-muted-foreground">
            {v.summary(formatNumber(summary.files), formatBytes(summary.bytes), formatNumber(summary.expiring_soon),
                       formatNumber(summary.importing))}
            {summary.oldest_at ? ` · ${v.oldest(formatDateTime(summary.oldest_at))}` : ""}
          </p>
        )}
        {summary && summary.delete_failures > 0 && <p className="text-destructive">{v.deleteFailures(formatNumber(summary.delete_failures))}</p>}
        {summary?.over_warning && <p className="text-warning">{v.overWarning(formatBytes(summary.warning_bytes))}</p>}
        {lastTest && (
          <p className="text-muted-foreground">
            {v.lastTest(formatDateTime(lastTest.at), lastTest.metadata.status === "ok" ? s.testStatus.ok : s.testStatus.error)}
          </p>
        )}
      </div>
      <SettingsCard key={`movie-general-${version}`} section="movie_sources" title={v.groups.general} labels={s.labels}
                    description={<span className="text-xs text-muted-foreground">{v.generalHint}</span>}
                    settings={group("general")} />
      <SettingsCard key={`movie-drive-${version}`} section="movie_sources" title={v.groups.drive} labels={s.labels}
                    description={<span className="text-xs text-muted-foreground">{v.driveHint}</span>}
                    settings={group("drive")} footer={<DriveTestButton />}
                    transform={{ "movie_sources.drive.warning_bytes": gigabytes }} />
      <SettingsCard key={`movie-retention-${version}`} section="movie_sources" title={v.groups.retention} labels={s.labels}
                    settings={group("retention")} />
      <SettingsCard key={`movie-limits-${version}`} section="movie_sources" title={v.groups.limits} labels={s.labels}
                    settings={group("limits")} transform={{ "movie_sources.max_source_bytes": gigabytes }} />
      <SettingsCard key={`movie-paths-${version}`} section="movie_sources" title={v.groups.paths} labels={s.labels}
                    description={<span className="text-xs text-muted-foreground">{v.pathsHint}</span>}
                    settings={group("paths")} />
      <SettingsCard key={`movie-analysis-${version}`} section="movie_sources" title={v.groups.analysis} labels={s.labels}
                    settings={group("analysis")} />
      <AdminMovieSources />
    </div>
  );
}

function ProviderTestButton({ provider }: { provider: string }) {
  const { t } = useI18n();
  const s = t.admin.system;
  const showError = useErrorToast();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ProviderTest | null>(null);
  const describe = (part: { status: string; code?: string }) =>
    `${s.testStatus[part.status as keyof typeof s.testStatus] ?? part.status}${part.code ? ` (${s.testCodes[part.code as keyof typeof s.testCodes] ?? part.code})` : ""}`;
  return (
    <>
      <Button type="button" variant="outline" size="sm" disabled={busy} onClick={async () => {
        setBusy(true);
        try {
          setResult(await api<ProviderTest>(`admin/system-config/ai/${provider}/test`, { method: "POST" }));
        } catch (error) {
          showError(error);
        } finally {
          setBusy(false);
        }
      }}>
        {busy ? <Loader2 className="size-3.5 animate-spin" /> : <ShieldCheck className="size-3.5" />}
        {s.testConnection}
      </Button>
      {result && (
        <span className="text-[11px]">
          {s.local}: <span className={STATUS_TONE[result.local.status]}>{describe(result.local)}</span> · {s.remote}:{" "}
          <span className={STATUS_TONE[result.remote.status]}>{describe(result.remote)}</span>
        </span>
      )}
    </>
  );
}

function CopyLine({ value }: { value: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-1.5">
      <code className="min-w-0 flex-1 truncate rounded bg-surface-2 px-2 py-1 text-xs" title={value}>{value || "—"}</code>
      <Button type="button" variant="ghost" size="icon" className="size-7 shrink-0" aria-label={t.admin.gateways.copy}
              disabled={!value} onClick={() => { void navigator.clipboard?.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
        {copied ? <Check className="size-3.5 text-success" /> : <Copy className="size-3.5" />}
      </Button>
    </div>
  );
}

function ProviderStatus({ data, provider }: { data: SystemConfigOverview; provider: string }) {
  const { t, formatDateTime } = useI18n();
  const s = t.admin.system;
  const status = data.ai_status?.[provider];
  const models = data.models_in_use[provider] ?? 0;
  if (!status) return <>{s.modelsInUse(models)}</>;
  const state = !status.enabled ? "off" : status.configured ? "ready" : "missing";
  return (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
      <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-medium", state === "ready" ? "bg-success/15 text-success"
        : state === "off" ? "bg-surface-2 text-muted-foreground" : "bg-warning/15 text-warning")}>{s.providerState[state]}</span>
      <span>{s.modelsInUse(models)}</span>
      {status.last_test && (
        <span>{s.lastTest(formatDateTime(status.last_test.at),
                          s.testStatus[status.last_test.remote as keyof typeof s.testStatus] ?? status.last_test.remote ?? "—")}</span>
      )}
    </span>
  );
}

function SecurityPanel({ data }: { data: SystemConfigOverview }) {
  const { t } = useI18n();
  const s = t.admin.system;
  const key = data.master_key;
  const tone = key.problem ? "text-destructive" : key.source === "legacy_env" || key.permissions_ok === false ? "text-warning" : "text-success";
  return (
    <div className="panel space-y-3 p-4 text-sm">
      <h3 className="text-sm font-semibold">{s.masterKey}</h3>
      <p className={cn("text-sm font-medium", tone)}>
        {key.problem ? s.keyProblems[key.problem as keyof typeof s.keyProblems] ?? key.problem : s.keySources[key.source]}
      </p>
      <dl className="grid gap-1 text-xs sm:grid-cols-[10rem_1fr]">
        <dt className="text-muted-foreground">{s.keyPath}</dt><dd className="font-mono">{key.path ?? "—"}</dd>
        <dt className="text-muted-foreground">{s.keyPermissions}</dt>
        <dd>{key.permissions_ok === null ? "—" : key.permissions_ok ? "600" : s.keyPermissionsOpen}</dd>
        <dt className="text-muted-foreground">{s.keyLegacy}</dt>
        <dd>{key.legacy_env_set ? (key.legacy_env_matches === false ? s.keyLegacyDiffers : s.keyLegacySet) : s.keyLegacyUnset}</dd>
      </dl>
      <div className="space-y-1 rounded-lg border border-border bg-surface-2/50 p-3 text-xs">
        <p>{s.keyHowTo}</p>
        <code className="block whitespace-pre-wrap font-mono">python -m app.master_key status{"\n"}sudo -u reelforge python -m app.master_key init</code>
        <p className="text-muted-foreground">{s.keyBackup}</p>
      </div>
      <p className="text-xs text-muted-foreground">{s.bootstrapOnly}</p>
      {data.legacy_in_use && (
        <div className="space-y-1 border-t border-border pt-3 text-xs">
          <h4 className="font-semibold">{s.legacyTitle}</h4>
          {data.legacy_in_use.length === 0 ? <p className="text-success">{s.legacyNone}</p> : (
            <>
              <p className="text-warning">{s.legacySome(data.legacy_in_use.length)}</p>
              <ul className="flex flex-wrap gap-1">
                {data.legacy_in_use.map((key) => (
                  <li key={key} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[10px]">
                    {s.labels[key as keyof typeof s.labels] ?? key}
                  </li>
                ))}
              </ul>
            </>
          )}
          {data.runtime_file?.path && <p className="text-muted-foreground">{s.runtimeFile(data.runtime_file.path, data.runtime_file.values)}</p>}
          {data.cache_seconds !== undefined && <p className="text-muted-foreground">{s.cacheNote(data.cache_seconds)}</p>}
        </div>
      )}
      {data.environment && (
        <details className="border-t border-border pt-3 text-xs">
          <summary className="cursor-pointer select-none font-semibold">{s.envTitle}</summary>
          <p className="mt-1 text-muted-foreground">{s.envHint}</p>
          <ul className="mt-1 space-y-0.5">
            {data.environment.filter((row) => row.set).map((row) => (
              <li key={row.name} className="flex flex-wrap gap-2">
                <code className="font-mono">{row.name}</code>
                <span className={cn("rounded px-1.5 text-[10px]", row.category === "bootstrap" ? "bg-success/15 text-success"
                  : row.category === "legacy" ? "bg-warning/15 text-warning" : "bg-surface-2 text-muted-foreground")}>
                  {s.envCategories[row.category]}
                </span>
                <span className="text-muted-foreground">{row.reason}</span>
              </li>
            ))}
            {!data.environment.some((row) => row.set) && <li className="text-muted-foreground">{s.envNone}</li>}
          </ul>
        </details>
      )}
    </div>
  );
}

function GeneralPanel() {
  const { t } = useI18n();
  const s = t.admin.system;
  const settings = useSettings();
  const client = useQueryClient();
  const showError = useErrorToast();
  const system = settings.data?.system;
  const [busy, setBusy] = useState(false);
  if (!system) return <Loader2 className="size-4 animate-spin text-muted-foreground" />;
  return (
    <form className="panel space-y-3 p-4" onSubmit={async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      setBusy(true);
      try {
        await api("settings/system", jsonRequest("PUT", {
          frontend_origin: String(form.get("frontend_origin") ?? ""),
          secure_cookies: form.get("secure_cookies") === "on",
          trial_project_limit: Number(form.get("trial_project_limit")),
          registration_enabled: form.get("registration_enabled") === "on",
        }));
        await Promise.all([client.invalidateQueries({ queryKey: keys.settings }), client.invalidateQueries({ queryKey: keys.systemConfig })]);
        toast.success(s.saved);
      } catch (error) {
        showError(error);
      } finally {
        setBusy(false);
      }
    }}>
      <h3 className="text-sm font-semibold">{s.nav.general}</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1 sm:col-span-2">
          <label htmlFor="frontend_origin" className="text-xs font-medium">{s.general.frontendOrigin}</label>
          <Input id="frontend_origin" name="frontend_origin" defaultValue={String(system.frontend_origin ?? "")} className="h-8 bg-surface-2 font-mono text-xs" />
          <p className="text-[11px] text-muted-foreground">{s.general.frontendOriginHint}</p>
          {system.local_override && (
            <p className="rounded-md border border-warning/40 bg-warning/10 px-2 py-1.5 text-[11px]">
              {s.general.localOverride(system.local_override.frontend_origin, system.local_override.secure_cookies)}
            </p>
          )}
        </div>
        <div className="space-y-1">
          <label htmlFor="trial_project_limit" className="text-xs font-medium">{s.general.trialProjects}</label>
          <Input id="trial_project_limit" name="trial_project_limit" type="number" min={1} max={10000}
                 defaultValue={Number(system.trial_project_limit ?? 3)} className="h-8 bg-surface-2 text-xs" />
        </div>
        <label className="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2 text-xs">
          {s.general.secureCookies}
          <input type="checkbox" name="secure_cookies" defaultChecked={Boolean(system.secure_cookies)} className="size-4" />
        </label>
        <label className="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2 text-xs">
          {s.general.registration}
          <input type="checkbox" name="registration_enabled" defaultChecked={Boolean(system.registration_enabled)} className="size-4" />
        </label>
      </div>
      <Button type="submit" size="sm" disabled={busy}>{busy && <Loader2 className="size-3.5 animate-spin" />}{s.save}</Button>
    </form>
  );
}

function EmailPanel({ data, version }: { data: SystemConfigOverview; version: string }) {
  const { t } = useI18n();
  const s = t.admin.system;
  const e = t.adminV1.email;
  const showError = useErrorToast();
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const group = (name: string) => (data.sections.email ?? []).filter((x) => x.group === name);
  const problem = data.email?.problem ?? null;

  async function test() {
    setBusy(true);
    setResult(null);
    try {
      const answer = await api<{ ok: boolean; error: string | null; to: string }>(
        "admin/system-config/email/test", jsonRequest("POST", { to: to.trim() || null }));
      setResult(answer.ok ? { ok: true, text: e.testOk(answer.to) }
        : { ok: false, text: e.testFailed(e.errors[answer.error ?? ""] ?? answer.error ?? "") });
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="panel space-y-1 p-4 text-xs">
        <p className="text-sm font-semibold">{e.title}</p>
        <p className="text-muted-foreground">{e.hint}</p>
        <p className={cn("text-sm font-medium", problem ? (problem === "disabled" ? "text-warning" : "text-destructive") : "text-success")}>
          {e.state[problem ?? "ok"] ?? problem}
        </p>
        {data.email && <p className="text-muted-foreground">sent {data.email.stats.sent} · queued {data.email.stats.queued} · failed {data.email.stats.failed}</p>}
      </div>
      <SettingsCard key={`email-sender-${version}`} section="email" title={e.sender} labels={s.labels} settings={group("sender")}
                    footer={
                      <div className="flex flex-wrap items-end gap-2">
                        <div className="min-w-[14rem] flex-1">
                          <label htmlFor="email-test-to" className="text-xs font-medium">{e.testTo}</label>
                          <Input id="email-test-to" type="email" value={to} onChange={(event) => setTo(event.target.value)}
                                 className="h-8 bg-surface-2 text-xs" />
                        </div>
                        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void test()}>
                          {busy && <Loader2 className="size-3.5 animate-spin" />}{e.test}
                        </Button>
                        {result && <p role="status" className={cn("w-full text-xs", result.ok ? "text-success" : "text-destructive")}>{result.text}</p>}
                      </div>
                    } />
      <SettingsCard key={`email-smtp-${version}`} section="email" title={e.smtp} labels={s.labels} settings={group("smtp")} />
      <SettingsCard key={`email-resend-${version}`} section="email" title={e.resend} labels={s.labels} settings={group("resend")} />
    </div>
  );
}

function BackupsPanel({ data, version }: { data: SystemConfigOverview; version: string }) {
  const { t, formatDateTime } = useI18n();
  const s = t.admin.system;
  const b = t.adminV1.backups;
  const k = t.adminV1.masterKeyBackup;
  const client = useQueryClient();
  const showError = useErrorToast();
  const backups = useBackups(true);
  const [busy, setBusy] = useState(false);
  const status = backups.data;
  const keyCheck = status?.master_key_backup;

  async function confirmKey(confirmed: boolean) {
    setBusy(true);
    try {
      await api("admin/master-key/backup-confirmation", jsonRequest("PUT", { confirmed }));
      await Promise.all([client.invalidateQueries({ queryKey: keys.backups }),
                         client.invalidateQueries({ queryKey: keys.systemReadiness })]);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="panel space-y-2 p-4 text-sm">
        <h3 className="text-sm font-semibold">{b.title}</h3>
        <p className="text-xs text-muted-foreground">{b.hint}</p>
        {backups.isPending ? <Loader2 className="size-4 animate-spin text-muted-foreground" /> :
          backups.isError ? <p className="text-destructive">{errorText(backups.error, t)}</p> : status && (
            <>
              <dl className="grid gap-1 text-xs sm:grid-cols-[12rem_1fr]">
                <dt className="text-muted-foreground">{b.lastSuccess}</dt>
                <dd className={status.last_success ? "" : "text-destructive"}>
                  {status.last_success ? `${formatDateTime(status.last_success.at)} · ${b.age(status.last_success.age_hours)} · ${status.last_success.file} · ${formatBytes(status.last_success.bytes)}` : b.none}
                </dd>
                <dt className="text-muted-foreground">{b.lastFailure}</dt>
                <dd className={status.last_failure ? "text-warning" : ""}>
                  {status.last_failure ? `${formatDateTime(status.last_failure.at)} · ${status.last_failure.error}` : "—"}
                </dd>
                <dt className="text-muted-foreground">{s.storage.current}</dt><dd className="font-mono">{status.directory}</dd>
                <dt className="text-muted-foreground">{b.settingsTitle}</dt>
                <dd>{b.retention(status.retention.daily, status.retention.weekly, status.retention.monthly)}</dd>
              </dl>
              {status.runs.length > 0 && (
                <details className="text-xs">
                  <summary className="cursor-pointer select-none font-medium">{b.runs}</summary>
                  <ul className="mt-1 space-y-0.5">
                    {status.runs.map((run) => (
                      <li key={run.started_at} className="flex flex-wrap gap-2">
                        <span>{formatDateTime(run.started_at)}</span>
                        <span className={run.status === "failed" ? "text-destructive" : run.status === "succeeded" ? "text-success" : ""}>
                          {b.statuses[run.status]}
                        </span>
                        {run.file && <span className="font-mono text-muted-foreground">{run.file}</span>}
                        {run.error && <span className="text-muted-foreground">{run.error}</span>}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          )}
      </div>
      <div className="panel space-y-2 p-4 text-sm">
        <h3 className="text-sm font-semibold">{k.title}</h3>
        <p className="text-xs text-muted-foreground">{k.hint}</p>
        {keyCheck?.detail === "key_changed" && <p className="text-xs text-warning">{k.changed}</p>}
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="size-4" disabled={busy || keyCheck?.detail === "no_key"}
                 checked={keyCheck?.status === "ok"} onChange={(event) => void confirmKey(event.target.checked)} />
          {k.confirm}
        </label>
        {keyCheck?.status === "ok" && typeof keyCheck.confirmed_at === "string" && (
          <p className="text-xs text-muted-foreground">{k.confirmed(String(keyCheck.confirmed_by ?? "—"), formatDateTime(keyCheck.confirmed_at))}</p>
        )}
      </div>
      <SettingsCard key={`backups-${version}`} section="backups" title={b.settingsTitle} labels={s.labels}
                    settings={data.sections.backups ?? []} />
    </div>
  );
}

/** Admin → System settings: everything the server used to read from .env.runtime, edited here, never a secret shown. */
/** ``initialSection`` opens one section (Admin → Overview links to Backups or Email). */
export function AdminSystem({ initialSection }: { initialSection?: string | null } = {}) {
  const { t } = useI18n();
  const s = t.admin.system;
  const config = useSystemConfig(true);
  const [nav, setNav] = useState<Nav>((NAV as readonly string[]).includes(initialSection ?? "") ? (initialSection as Nav) : "security");
  const data = config.data;
  const group = (section: SystemSection, name: string) => (data?.sections[section] ?? []).filter((x) => x.group === name);
  const keyed = (data: SystemConfigOverview) =>
    Object.values(data.sections).flat().map((x) => `${x.key}:${x.updated_at}:${x.source}`).join("|");

  let content: ReactNode = <Loader2 className="size-4 animate-spin text-muted-foreground" />;
  if (data) {
    const version = keyed(data);
    const gigabytes = { show: (value: number) => String(Math.round((value / GIB) * 100) / 100),
                        store: (text: string) => Math.round(Number(text) * GIB), unit: "GB" };
    if (nav === "security") content = (
      <div className="space-y-3">
        <SecurityPanel data={data} />
        <SettingsCard key={`proxy-${version}`} section="security" title={t.adminV1.proxy.title} labels={s.labels}
                      description={<span className="text-xs text-muted-foreground">{t.adminV1.proxy.hint}</span>}
                      settings={data.sections.security ?? []} />
      </div>
    );
    else if (nav === "email") content = <EmailPanel data={data} version={version} />;
    else if (nav === "backups") content = <BackupsPanel data={data} version={version} />;
    else if (nav === "movie_sources") content = <MovieSourcesPanel data={data} version={version} />;
    else if (nav === "general") content = <GeneralPanel />;
    else if (nav === "ai") content = (
      <div className="space-y-3">
        <p className="text-xs text-muted-foreground">{s.aiHint}</p>
        {AI_PROVIDERS.map((provider) => (
          <SettingsCard key={`${provider}-${version}`} section="ai" title={s.providers[provider]} labels={s.labels}
                        description={<ProviderStatus data={data} provider={provider} />} settings={group("ai", provider)}
                        footer={<ProviderTestButton provider={provider} />} />
        ))}
      </div>
    );
    else if (nav === "social") content = (
      <div className="space-y-3">
        <p className="text-xs text-muted-foreground">{s.socialHint}</p>
        {CHANNELS.map((channel) => (
          <SettingsCard key={`${channel}-${version}`} section="social" title={s.channels[channel]} labels={s.labels}
                        settings={group("social", channel)}
                        extra={<div className="space-y-1"><p className="text-[11px] text-muted-foreground">{s.redirectInUse}</p>
                          <CopyLine value={data.redirects[channel]} /></div>} />
        ))}
      </div>
    );
    else if (nav === "storage") content = (
      <div className="space-y-3">
        <div className="panel space-y-1 p-4 text-xs">
          <p><span className="text-muted-foreground">{s.storage.current}: </span><span className="font-mono">{data.storage.root}</span></p>
          <p className="text-muted-foreground">{s.storage.summary(data.storage.files, data.storage.disk ? formatBytes(data.storage.disk.free_bytes) : "—")}</p>
          <p className="text-warning">{s.storage.noMove}</p>
        </div>
        <SettingsCard key={`root-${version}`} section="storage" title={s.storage.rootTitle} labels={s.labels}
                      settings={data.sections.storage.filter((x) => x.key === "storage.root")} />
        <SettingsCard key={`quota-${version}`} section="storage" title={s.storage.limitsTitle} labels={s.labels}
                      settings={data.sections.storage.filter((x) => x.key !== "storage.root")}
                      transform={{ "storage.quota_ceiling_bytes": gigabytes }} />
      </div>
    );
    else content = (
      <div className="space-y-3">
        {nav === "credits" && <p className="text-xs text-muted-foreground">{s.creditsHint}</p>}
        <SettingsCard key={`${nav}-${version}`} section={nav} title={s.nav[nav]} labels={s.labels} settings={data.sections[nav]} />
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 lg:flex-row">
      <nav className="scrollbar-thin flex shrink-0 gap-1 overflow-x-auto lg:w-44 lg:flex-col lg:overflow-visible" aria-label={s.title}>
        {NAV.map((key) => (
          <button key={key} type="button" onClick={() => setNav(key)} aria-current={nav === key ? "page" : undefined}
                  className={cn("whitespace-nowrap rounded-md px-3 py-1.5 text-left text-sm",
                    nav === key ? "bg-primary/15 font-medium text-primary" : "text-muted-foreground hover:bg-surface-2")}>
            {s.nav[key]}
          </button>
        ))}
      </nav>
      <div className="scrollbar-thin relative min-h-0 flex-1 overflow-y-auto pr-1">
        {data && !data.migrated && <p className="mb-3 text-xs text-warning">{s.notMigrated}</p>}
        {config.isError ? <p className="text-sm text-destructive">{errorText(config.error, t)}</p> : content}
      </div>
    </div>
  );
}
