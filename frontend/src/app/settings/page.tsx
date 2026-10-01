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
import { FieldLabel, OptionChips, PageHeader, SettingRow } from "@/components/reelforge/primitives";
import { DefaultModelsForm, StorageUsagePanel } from "@/components/reelforge/default-models";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useDocumentTitle, useSearchParam } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { LOCALES, LOCALE_NAMES, isLocale } from "@/lib/i18n/config";
import { keys, useDashboard, useSettings } from "@/lib/queries";
import { formatBytes } from "@/lib/studio";
import type { ContentPlatform, ContentTone, SystemSettings, WorkspaceSettings } from "@/lib/types";

const PLATFORMS: ContentPlatform[] = ["generic", "youtube", "youtube_shorts", "tiktok", "facebook"];
const TONES: ContentTone[] = [
  "neutral",
  "casual",
  "professional",
  "cinematic",
  "storytelling",
  "documentary",
  "dramatic",
  "funny",
];

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
  const [displayName, setDisplayName] = useState("");
  const [tab, setTab] = useState("defaults");
  const [saving, setSaving] = useState<"workspace" | "system" | "profile" | null>(null);
  // ?tab=storage opens a tab directly (the storage warning links here).
  const requestedTab = useSearchParam("tab");
  useEffect(() => {
    if (requestedTab) setTab(requestedTab);
  }, [requestedTab]);

  useEffect(() => {
    if (settings.data) {
      setWorkspace(settings.data.workspace);
      setSystem(settings.data.system);
      setDisplayName(settings.data.profile?.display_name ?? "");
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

  async function saveProfile() {
    setSaving("profile");
    try {
      await api("settings/profile", jsonRequest("PUT", { display_name: displayName.trim() || null }));
      await client.invalidateQueries({ queryKey: keys.settings });
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
      const { storage_dir: _ignored, storage_dir_source: _source, ...body } = system;
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
          tab === "general" ? null : tab === "system" ? (
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
              <FieldLabel htmlFor="settings-name">{s.general.displayName}</FieldLabel>
              <div className="flex items-center gap-2">
                <Input
                  id="settings-name"
                  value={displayName}
                  maxLength={80}
                  placeholder={dashboard?.user.email.split("@")[0] ?? ""}
                  onChange={(e) => setDisplayName(e.target.value)}
                  className="bg-surface"
                />
                <Button
                  variant="outline"
                  onClick={() => void saveProfile()}
                  disabled={saving !== null || displayName.trim() === (settings.data?.profile?.display_name ?? "")}
                >
                  {saving === "profile" && <Loader2 className="size-4 animate-spin" />}
                  {t.common.save}
                </Button>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{s.general.displayNameHint}</p>
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
            <div>
              <FieldLabel>{s.defaults.platform}</FieldLabel>
              <OptionChips
                options={PLATFORMS.map((value) => ({ value, label: t.config.options.platform?.[value] ?? value }))}
                value={[workspace.default_platform]}
                onChange={([next]) => next && setWorkspace({ ...workspace, default_platform: next as ContentPlatform })}
              />
            </div>
            <div>
              <FieldLabel>{s.defaults.tone}</FieldLabel>
              <OptionChips
                options={TONES.map((value) => ({ value, label: t.config.options.tone?.[value] ?? value }))}
                value={[workspace.default_tone]}
                onChange={([next]) => next && setWorkspace({ ...workspace, default_tone: next as ContentTone })}
              />
            </div>
            <div>
              <FieldLabel htmlFor="ws-duration">{s.defaults.length}</FieldLabel>
              <Input
                id="ws-duration"
                type="number"
                min={5}
                max={3600}
                value={workspace.default_duration ?? ""}
                placeholder={s.defaults.lengthAuto}
                onChange={(e) =>
                  setWorkspace({ ...workspace, default_duration: e.target.value ? Number(e.target.value) : null })
                }
                className="max-w-[12rem] bg-surface"
              />
              <p className="mt-1 text-xs text-muted-foreground">{s.defaults.lengthHint}</p>
            </div>
            <p className="text-xs text-muted-foreground">{s.defaults.appliesHint}</p>
          </div>
        </TabsContent>

        <TabsContent value="ai" className="mt-5">
          <div className="panel max-w-xl space-y-4 p-5 text-sm">
            <DefaultModelsForm />
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
            <div>
              <FieldLabel htmlFor="ws-publish-time">{s.publishing.publishTime}</FieldLabel>
              <Input
                id="ws-publish-time"
                type="time"
                value={workspace.default_publish_time ?? ""}
                onChange={(e) => setWorkspace({ ...workspace, default_publish_time: e.target.value || null })}
                className="max-w-[10rem] bg-surface"
              />
              <p className="mt-1 text-xs text-muted-foreground">{s.publishing.publishTimeHint}</p>
            </div>
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
                <p className="mt-1 text-xs text-muted-foreground">
                  {system.storage_dir_source === "admin" ? s.storage.pathFromAdmin
                    : system.storage_dir_source === "environment" ? s.storage.pathFromEnv : s.storage.pathHint}
                </p>
              </div>
            )}
          </div>
        </TabsContent>

        <TabsContent value="security" className="mt-5">
          <div className="panel max-w-xl space-y-4 p-5 text-sm">
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
