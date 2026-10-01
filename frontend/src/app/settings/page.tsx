"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  FieldLabel,
  OptionChips,
  PageHeader,
  SettingRow,
  SoonBadge,
} from "@/components/reelforge/primitives";
import { DefaultModelsForm, StorageUsagePanel } from "@/components/reelforge/default-models";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { LOCALES, LOCALE_NAMES, isLocale } from "@/lib/i18n/config";
import { keys, useDashboard, useSettings } from "@/lib/queries";
import { formatBytes } from "@/lib/studio";
import type { SystemSettings, WorkspaceSettings } from "@/lib/types";

const Soon = ({ label }: { label: string }) => (
  <span className="flex flex-wrap items-center gap-2">
    {label} <SoonBadge />
  </span>
);

export default function SettingsPage() {
  const { t, locale, setLocale } = useI18n();
  useDocumentTitle(t.settings.title);
  const router = useRouter();
  const client = useQueryClient();
  const showError = useErrorToast();
  const { data: dashboard } = useDashboard();
  const settings = useSettings();
  const [workspace, setWorkspace] = useState<WorkspaceSettings | null>(null);
  const [system, setSystem] = useState<SystemSettings | null>(null);
  const [tab, setTab] = useState("defaults");
  const [saving, setSaving] = useState<"workspace" | "system" | null>(null);

  useEffect(() => {
    if (settings.data) {
      setWorkspace(settings.data.workspace);
      setSystem(settings.data.system);
    }
  }, [settings.data]);

  const s = t.settings;
  const dirty = Boolean(workspace && settings.data && JSON.stringify(workspace) !== JSON.stringify(settings.data.workspace));
  const storedBytes = (dashboard?.assets ?? []).reduce((sum, a) => sum + a.bytes, 0);
  const tabs = [
    ...(["general", "workspace", "defaults", "ai", "publishing", "storage", "security"] as const),
    ...(dashboard?.is_admin ? (["system"] as const) : []),
  ];

  async function saveWorkspace() {
    if (!workspace) return;
    setSaving("workspace");
    try {
      await api("settings/workspace", jsonRequest("PUT", workspace));
      await client.invalidateQueries({ queryKey: keys.settings });
      await client.invalidateQueries({ queryKey: ["readiness"] });
      toast.success(s.savedToast);
    } catch (error) {
      showError(error);
    } finally {
      setSaving(null);
    }
  }

  async function saveSystem() {
    if (!system) return;
    setSaving("system");
    try {
      const { storage_dir: _ignored, ...body } = system;
      await api("settings/system", jsonRequest("PUT", body));
      await client.invalidateQueries({ queryKey: keys.settings });
      toast.success(s.savedToast);
    } catch (error) {
      showError(error);
    } finally {
      setSaving(null);
    }
  }

  async function signOut() {
    try {
      await api("logout", { method: "POST" });
      router.push("/");
      await client.resetQueries();
    } catch (error) {
      showError(error);
    }
  }

  if (!workspace) return <Loader2 className="mx-auto mt-10 size-5 animate-spin text-muted-foreground" />;

  return (
    <div className="space-y-6">
      <PageHeader
        title={s.title}
        subtitle={s.subtitle}
        actions={
          tab === "system" ? (
            <Button onClick={() => void saveSystem()} disabled={saving !== null}>
              {saving === "system" && <Loader2 className="size-4 animate-spin" />}
              {s.system.save}
            </Button>
          ) : (
            <Button onClick={() => void saveWorkspace()} disabled={!dirty || saving !== null}>
              {saving === "workspace" && <Loader2 className="size-4 animate-spin" />}
              {t.common.saveChanges}
            </Button>
          )
        }
      />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="h-auto flex-wrap">
          {tabs.map((key) => (
            <TabsTrigger key={key} value={key}>
              {s.tabs[key]}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="general" className="mt-5">
          <div className="panel max-w-xl space-y-4 p-5">
            <div>
              <FieldLabel>{s.general.displayName}</FieldLabel>
              <div className="flex items-center gap-2">
                <Input disabled value={dashboard?.user.email.split("@")[0] ?? ""} className="bg-surface-2" />
                <SoonBadge />
              </div>
            </div>
            <div>
              <FieldLabel htmlFor="settings-email">{s.general.email}</FieldLabel>
              <Input id="settings-email" readOnly value={dashboard?.user.email ?? ""} className="bg-surface-2" />
            </div>
            <div>
              <FieldLabel>{s.general.language}</FieldLabel>
              <OptionChips
                options={LOCALES.map((code) => ({ value: code, label: LOCALE_NAMES[code] }))}
                value={[locale]}
                onChange={([next]) => {
                  if (isLocale(next) && next !== locale) {
                    setLocale(next);
                    toast.success(t.settings.languageChanged);
                  }
                }}
              />
              <p className="mt-2 text-xs text-muted-foreground">{s.general.languageHint}</p>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="workspace" className="mt-5">
          <div className="panel max-w-xl space-y-4 p-5">
            <div>
              <FieldLabel htmlFor="ws-name">{s.workspace.name}</FieldLabel>
              <Input id="ws-name" readOnly value={dashboard?.workspace.name ?? ""} className="bg-surface-2" />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <FieldLabel>{s.workspace.plan}</FieldLabel>
                <p className="text-sm">{dashboard?.workspace.plan.toUpperCase()}</p>
              </div>
              <div>
                <FieldLabel>{s.workspace.id}</FieldLabel>
                <p className="break-all font-mono text-xs text-muted-foreground">{dashboard?.workspace.id}</p>
              </div>
            </div>
            <SettingRow label={<Soon label={s.workspace.teammates} />}>
              <Switch disabled />
            </SettingRow>
          </div>
        </TabsContent>

        <TabsContent value="defaults" className="mt-5">
          <div className="panel max-w-2xl space-y-5 p-5">
            <div>
              <FieldLabel>{s.defaults.language}</FieldLabel>
              <OptionChips
                options={LOCALES.map((code) => ({ value: code, label: LOCALE_NAMES[code] }))}
                value={[workspace.default_language]}
                onChange={([next]) => isLocale(next) && setWorkspace({ ...workspace, default_language: next })}
              />
            </div>
            <div>
              <FieldLabel>{s.defaults.orientation}</FieldLabel>
              <OptionChips
                options={(["vertical", "horizontal", "square"] as const).map((o) => ({
                  value: o,
                  label: s.defaults.orientations[o],
                }))}
                value={[workspace.video_orientation]}
                onChange={([next]) =>
                  setWorkspace({ ...workspace, video_orientation: next as WorkspaceSettings["video_orientation"] })
                }
              />
              {workspace.video_orientation === "square" && (
                <p className="mt-2 text-xs text-warning">{s.defaults.squareNote}</p>
              )}
            </div>
            {(
              [
                [s.defaults.platform, s.defaults.platforms],
                [s.defaults.tone, s.defaults.tones],
                [s.defaults.length, s.defaults.lengths],
              ] as const
            ).map(([label, options]) => (
              <div key={label}>
                <FieldLabel>
                  <Soon label={label} />
                </FieldLabel>
                <OptionChips options={options} value={[options[0]!]} onChange={() => undefined} disabled />
              </div>
            ))}
          </div>
        </TabsContent>

        <TabsContent value="ai" className="mt-5">
          <div className="panel max-w-xl space-y-4 p-5 text-sm">
            {[s.ai.recaps, s.ai.thumbnails, s.ai.providerIds].map((label) => (
              <SettingRow key={label} label={<Soon label={label} />}>
                <Switch disabled />
              </SettingRow>
            ))}
            <div className="border-t border-border pt-4">
              <DefaultModelsForm />
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
              <p className="text-xs text-muted-foreground">{s.ai.modelsHint}</p>
              <Button asChild variant="outline" size="sm">
                <Link href="/models">{s.ai.manageModels}</Link>
              </Button>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="publishing" className="mt-5">
          <div className="panel max-w-xl space-y-4 p-5 text-sm">
            <SettingRow label={s.publishing.review} hint={s.publishing.reviewHint}>
              <Switch
                checked={workspace.approval_required}
                onCheckedChange={(v) => setWorkspace({ ...workspace, approval_required: v })}
              />
            </SettingRow>
            <SettingRow label={<Soon label={s.publishing.autoSchedule} />}>
              <Switch disabled />
            </SettingRow>
          </div>
        </TabsContent>

        <TabsContent value="storage" className="mt-5">
          <div className="panel max-w-xl space-y-4 p-5 text-sm">
            <p>{s.storage.used(formatBytes(storedBytes), dashboard?.assets.length ?? 0)}</p>
            <StorageUsagePanel />
            {system && (
              <div>
                <FieldLabel>{s.storage.path}</FieldLabel>
                <p className="break-all font-mono text-xs">{system.storage_dir}</p>
                <p className="mt-1 text-xs text-muted-foreground">{s.storage.pathHint}</p>
              </div>
            )}
            <SettingRow label={<Soon label={s.storage.retention} />}>
              <Switch disabled />
            </SettingRow>
          </div>
        </TabsContent>

        <TabsContent value="security" className="mt-5">
          <div className="panel max-w-xl space-y-4 p-5 text-sm">
            <SettingRow label={<Soon label={s.security.twoFactor} />}>
              <Switch disabled />
            </SettingRow>
            <SettingRow label={s.security.signOut}>
              <Button variant="outline" size="sm" onClick={() => void signOut()}>
                {t.shell.signOut}
              </Button>
            </SettingRow>
          </div>
        </TabsContent>

        {system && (
          <TabsContent value="system" className="mt-5">
            <div className="panel max-w-xl space-y-4 p-5 text-sm">
              <div>
                <FieldLabel htmlFor="sys-origin">{s.system.origin}</FieldLabel>
                <Input
                  id="sys-origin"
                  type="url"
                  value={system.frontend_origin}
                  onChange={(e) => setSystem({ ...system, frontend_origin: e.target.value })}
                  className="bg-surface-2"
                />
              </div>
              <div>
                <FieldLabel htmlFor="sys-trial">{s.system.trialLimit}</FieldLabel>
                <Input
                  id="sys-trial"
                  type="number"
                  min={1}
                  max={10000}
                  value={system.trial_project_limit}
                  onChange={(e) => setSystem({ ...system, trial_project_limit: Number(e.target.value) })}
                  className="bg-surface-2"
                />
              </div>
              <SettingRow label={s.system.secureCookies}>
                <Switch checked={system.secure_cookies} onCheckedChange={(v) => setSystem({ ...system, secure_cookies: v })} />
              </SettingRow>
              <SettingRow label={s.system.registration}>
                <Switch
                  checked={system.registration_enabled}
                  onCheckedChange={(v) => setSystem({ ...system, registration_enabled: v })}
                />
              </SettingRow>
            </div>
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
