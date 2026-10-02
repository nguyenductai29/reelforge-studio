"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { QueryError } from "@/components/reelforge/query-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { FieldLabel, OptionChips, PageHeader, SettingRow } from "@/components/reelforge/primitives";
import { DefaultModelsForm, StorageUsagePanel } from "@/components/reelforge/default-models";
import { AccountSecurityPanel } from "@/components/reelforge/account-security";
import { WorkspaceMembersPanel } from "@/components/reelforge/workspace-members";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useDocumentTitle, useSearchParam } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { can } from "@/lib/permissions";
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
// A tab of panels: it sizes to its content, and only a window too short for it scrolls (inside, never the page).
const PANEL_TAB = "scrollbar-thin mt-3 min-h-0 flex-1 overflow-y-auto";
// A tab that lays out its own scrolling region (the members table): it only gives it the height.
const FILL_TAB = "mt-3 flex min-h-0 flex-1 flex-col";

/**
 * Settings fits the window like the admin console: the header (with the tab's save action) and the tabs stay put,
 * and only a tab's content scrolls when it is longer than the space left.
 */
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
  const [saving, setSaving] = useState<"workspace" | "system" | "profile" | "name" | null>(null);
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
  const m = t.team;
  const manage = can(dashboard, "settings.manage");
  const role = dashboard?.workspace.role;
  const dirty = Boolean(workspace && settings.data && JSON.stringify(workspace) !== JSON.stringify(settings.data.workspace));
  const storedBytes = (dashboard?.assets ?? []).reduce((sum, a) => sum + a.bytes, 0);
  const tabs = [
    ...(["general", "workspace", "members", "defaults", "ai", "publishing", "storage", "security"] as const),
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

  async function rename(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving("name");
    try {
      await api("workspace", jsonRequest("PUT", { name: new FormData(event.currentTarget).get("name") }));
      toast.success(m.renamed);
      await Promise.all([client.invalidateQueries({ queryKey: keys.dashboard }),
                         client.invalidateQueries({ queryKey: keys.members })]);
    } catch (error) {
      showError(error);
    } finally {
      setSaving(null);
    }
  }

  // Saved at once, on top of the saved settings: an unsaved draft of another tab is never sent with it.
  async function setEditorsPublish(value: boolean) {
    if (!settings.data) return;
    try {
      await api("settings/workspace", jsonRequest("PUT", { ...settings.data.workspace, editors_can_publish: value }));
      setWorkspace((current) => (current ? { ...current, editors_can_publish: value } : current));
      await Promise.all([client.invalidateQueries({ queryKey: keys.settings }),
                         client.invalidateQueries({ queryKey: keys.dashboard })]);
    } catch (error) {
      showError(error);
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

  if (!workspace && settings.isError) return <QueryError error={settings.error} onRetry={() => void settings.refetch()} />;
  if (!workspace) return <Loader2 className="mx-auto mt-10 size-5 animate-spin text-muted-foreground" />;

  // The tab's main save action lives in the header, outside any scrolling region; tabs whose actions apply at once
  // (members, security, the studio name, AI defaults with their own button) have none.
  const headerAction = tab === "system" ? (
    <Button onClick={() => void saveSystem()} disabled={saving !== null}>
      {saving === "system" && <Loader2 className="size-4 animate-spin" />}
      {s.system.save}
    </Button>
  ) : tab === "defaults" || tab === "publishing" ? (
    <Button onClick={() => void saveWorkspace()} disabled={!dirty || saving !== null || !manage}>
      {saving === "workspace" && <Loader2 className="size-4 animate-spin" />}
      {t.common.saveChanges}
    </Button>
  ) : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="shrink-0">
        <PageHeader compact title={s.title} subtitle={s.subtitle} actions={headerAction} />
      </div>
      <Tabs value={tab} onValueChange={setTab} className="mt-4 flex min-h-0 flex-1 flex-col">
        <TabsList className="scrollbar-thin h-auto w-full shrink-0 justify-start overflow-x-auto sm:w-fit">
          {tabs.map((key) => (
            <TabsTrigger key={key} value={key}>
              {s.tabs[key]}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="general" className={PANEL_TAB}>
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

        <TabsContent value="workspace" className={PANEL_TAB}>
          <div className="panel max-w-xl space-y-4 p-5">
            <div>
              <FieldLabel htmlFor="workspace-name">{s.workspace.name}</FieldLabel>
              {manage ? (
                <form className="flex items-center gap-2" onSubmit={rename}>
                  <Input id="workspace-name" name="name" key={dashboard?.workspace.name} defaultValue={dashboard?.workspace.name ?? ""}
                         required maxLength={100} className="bg-surface" />
                  <Button type="submit" variant="outline" disabled={saving !== null}>
                    {saving === "name" && <Loader2 className="size-4 animate-spin" />}
                    {m.rename}
                  </Button>
                </form>
              ) : (
                <Input id="workspace-name" readOnly value={dashboard?.workspace.name ?? ""} className="bg-surface-2" />
              )}
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
            {role && (
              <div>
                <FieldLabel>{m.yourRole}</FieldLabel>
                <p className="text-sm">{m.roles[role]} <span className="text-xs text-muted-foreground">· {m.roleHints[role]}</span></p>
              </div>
            )}
            {manage && (
              <SettingRow label={m.editorsPublish} hint={m.editorsPublishHint}>
                <Switch checked={workspace.editors_can_publish ?? true} onCheckedChange={(value) => void setEditorsPublish(value)}
                        aria-label={m.editorsPublish} />
              </SettingRow>
            )}
          </div>
        </TabsContent>

        <TabsContent value="defaults" className={PANEL_TAB}>
          <div className="panel max-w-4xl space-y-5 p-5">
            {/* Two balanced columns from medium screens; one below. */}
            <div className="grid gap-x-8 gap-y-5 md:grid-cols-2">
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
            </div>
            <p className="text-xs text-muted-foreground">{s.defaults.appliesHint}</p>
          </div>
        </TabsContent>

        <TabsContent value="ai" className={PANEL_TAB}>
          <div className="panel max-w-xl p-5 text-sm">
            <DefaultModelsForm
              footer={
                <span className="flex flex-wrap items-center gap-2">
                  <span className="text-xs text-muted-foreground">{s.ai.modelsHint}</span>
                  <Button asChild variant="outline" size="sm">
                    <Link href="/models">{s.ai.manageModels}</Link>
                  </Button>
                </span>
              }
            />
          </div>
        </TabsContent>

        <TabsContent value="publishing" className={PANEL_TAB}>
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

        <TabsContent value="storage" className={PANEL_TAB}>
          <div className="panel max-w-4xl p-4 text-sm">
            <StorageUsagePanel
              lead={<p>{s.storage.used(formatBytes(storedBytes), dashboard?.assets.length ?? 0)}</p>}
              aside={system && (
                <div>
                  <FieldLabel>{s.storage.path}</FieldLabel>
                  <p className="break-all font-mono text-xs">{system.storage_dir}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {system.storage_dir_source === "admin" ? s.storage.pathFromAdmin
                      : system.storage_dir_source === "environment" ? s.storage.pathFromEnv : s.storage.pathHint}
                  </p>
                </div>
              )}
            />
          </div>
        </TabsContent>

        <TabsContent value="members" className={FILL_TAB}>
          <WorkspaceMembersPanel />
        </TabsContent>

        <TabsContent value="security" className={PANEL_TAB}>
          <div className="space-y-4">
            <AccountSecurityPanel />
            <div className="panel max-w-xl space-y-4 p-5 text-sm">
              <SettingRow label={s.security.signOut}>
                <Button variant="outline" size="sm" onClick={() => void signOut()}>
                  {t.shell.signOut}
                </Button>
              </SettingRow>
            </div>
          </div>
        </TabsContent>

        {system && (
          <TabsContent value="system" className={PANEL_TAB}>
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
