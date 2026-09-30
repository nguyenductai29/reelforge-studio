"use client";

import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
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
import { EmptyState, FieldLabel, PageHeader, StatCard, StatusBadge } from "@/components/reelforge/primitives";
import { api, jsonRequest } from "@/lib/api";
import { errorText } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import type { Dictionary } from "@/lib/i18n/vi";
import { keys, useAdmin, useDashboard } from "@/lib/queries";
import type { Plan } from "@/lib/types";

type Confirm = { title: string; description: string; run: () => Promise<unknown> };

function PlanForm({ plan, busy, onSave }: { plan: Plan; busy: boolean; onSave: (body: object) => void }) {
  const { t } = useI18n();
  const p = t.admin.plans;
  const [active, setActive] = useState(plan.is_active);
  const number = (value: FormDataEntryValue | null) => (value ? Number(value) : null);
  return (
    <form
      className="panel space-y-4 p-5"
      onSubmit={(event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        onSave({
          name: form.get("name"),
          project_limit: number(form.get("project_limit")),
          workflow_limit: number(form.get("workflow_limit")),
          monthly_credits: Number(form.get("monthly_credits")),
          price_vnd: plan.code === "trial" ? null : number(form.get("price_vnd")),
          is_active: active,
        });
      }}
    >
      <div className="flex items-center justify-between">
        <span className="rounded-md bg-surface-2 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          {plan.code}
        </span>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <FieldLabel htmlFor={`${plan.code}-name`}>{p.name}</FieldLabel>
          <Input id={`${plan.code}-name`} name="name" defaultValue={plan.name} required maxLength={80} className="bg-surface-2" />
        </div>
        <div>
          <FieldLabel htmlFor={`${plan.code}-price`}>{p.price}</FieldLabel>
          <Input
            id={`${plan.code}-price`}
            name="price_vnd"
            type="number"
            min={2000}
            max={2000000000}
            defaultValue={plan.price_vnd ?? ""}
            placeholder={plan.code === "trial" ? t.billing.free : t.billing.notForSale}
            disabled={plan.code === "trial"}
            className="bg-surface-2"
          />
        </div>
        <div>
          <FieldLabel htmlFor={`${plan.code}-projects`}>{p.projectLimit}</FieldLabel>
          <Input
            id={`${plan.code}-projects`}
            name="project_limit"
            type="number"
            min={1}
            defaultValue={plan.project_limit ?? ""}
            placeholder={t.common.unlimited}
            required={plan.code === "trial"}
            className="bg-surface-2"
          />
        </div>
        <div>
          <FieldLabel htmlFor={`${plan.code}-workflows`}>{p.workflowLimit}</FieldLabel>
          <Input
            id={`${plan.code}-workflows`}
            name="workflow_limit"
            type="number"
            min={1}
            defaultValue={plan.workflow_limit ?? ""}
            placeholder={t.common.unlimited}
            className="bg-surface-2"
          />
        </div>
        <div>
          <FieldLabel htmlFor={`${plan.code}-credits`}>{p.monthlyCredits}</FieldLabel>
          <Input
            id={`${plan.code}-credits`}
            name="monthly_credits"
            type="number"
            min={0}
            defaultValue={plan.monthly_credits}
            required
            className="bg-surface-2"
          />
        </div>
        <label className="flex items-center justify-between gap-3 self-end text-sm">
          {p.active}
          <Switch checked={active} onCheckedChange={setActive} />
        </label>
      </div>
      <div className="flex justify-end">
        <Button type="submit" disabled={busy}>
          {p.save}
        </Button>
      </div>
    </form>
  );
}

export default function AdminPage() {
  const { t, formatDate, formatNumber } = useI18n();
  useDocumentTitle(t.admin.title);
  const client = useQueryClient();
  const { data: dashboard } = useDashboard();
  const admin = useAdmin(Boolean(dashboard?.is_admin));
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const a = t.admin;

  if (!dashboard?.is_admin) {
    return <EmptyState icon={ShieldCheck} title={a.title} description={t.errors.server["System admin required"] ?? ""} />;
  }
  const overview = admin.data;
  if (!overview) return <Loader2 className="mx-auto mt-10 size-5 animate-spin text-muted-foreground" />;

  async function mutate(run: () => Promise<unknown>, message: string = a.saved) {
    setBusy(true);
    try {
      await run();
      await client.invalidateQueries({ queryKey: keys.admin });
      toast.success(message);
      return true;
    } catch (error) {
      toast.error(errorText(error, t));
      return false;
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  }

  const owner = (id: string) => overview.users.find((user) => user.id === id)?.email ?? id;
  const subscription = (status: string) =>
    t.status.subscription[status as keyof Dictionary["status"]["subscription"]] ?? status;

  return (
    <div className="space-y-6">
      <PageHeader title={a.title} subtitle={a.subtitle} />
      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard label={a.stats.users} value={formatNumber(overview.users.length)} />
        <StatCard label={a.stats.studios} value={formatNumber(overview.workspaces.length)} />
        <StatCard label={a.stats.plans} value={formatNumber(overview.plans.length)} />
      </div>

      <Tabs defaultValue="users">
        <TabsList className="h-auto flex-wrap">
          {(["users", "create", "studios", "plans"] as const).map((key) => (
            <TabsTrigger key={key} value={key}>
              {a.tabs[key]}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="users" className="mt-5">
          <div className="panel divide-y divide-border">
            {overview.users.map((user) => (
              <div key={user.id} className="flex flex-wrap items-center gap-3 p-4 text-sm">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{user.email}</p>
                  <p className="text-xs text-muted-foreground">{user.is_admin ? a.users.admin : a.users.member}</p>
                </div>
                <StatusBadge
                  status={user.is_active ? "connected" : "failed"}
                  label={user.is_active ? a.users.active : a.users.locked}
                />
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() =>
                    setConfirm({
                      title: user.is_active ? a.users.lockTitle : a.users.unlockTitle,
                      description: user.is_active ? a.users.lockDescription(user.email) : a.users.unlockDescription(user.email),
                      run: () => api(`admin/users/${user.id}`, jsonRequest("PUT", { is_active: !user.is_active })),
                    })
                  }
                >
                  {user.is_active ? a.users.lock : a.users.unlock}
                </Button>
              </div>
            ))}
          </div>
        </TabsContent>

        <TabsContent value="create" className="mt-5">
          <form
            className="panel max-w-2xl space-y-4 p-5"
            onSubmit={(event) => {
              event.preventDefault();
              const form = event.currentTarget;
              const body = Object.fromEntries(new FormData(form));
              void mutate(() => api("admin/accounts", jsonRequest("POST", body)), a.create.created).then(
                (ok) => ok && form.reset(),
              );
            }}
          >
            <div>
              <p className="text-sm font-medium">{a.create.title}</p>
              <p className="mt-1 text-xs text-muted-foreground">{a.create.hint}</p>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <FieldLabel htmlFor="admin-email">{a.create.email}</FieldLabel>
                <Input id="admin-email" type="email" name="email" required className="bg-surface-2" />
              </div>
              <div>
                <FieldLabel htmlFor="admin-password">{a.create.password}</FieldLabel>
                <Input
                  id="admin-password"
                  type="password"
                  name="password"
                  minLength={12}
                  required
                  autoComplete="new-password"
                  className="bg-surface-2"
                />
              </div>
              <div>
                <FieldLabel htmlFor="admin-studio">{a.create.studio}</FieldLabel>
                <Input id="admin-studio" name="workspace_name" maxLength={100} required className="bg-surface-2" />
              </div>
              <div>
                <FieldLabel>{a.create.plan}</FieldLabel>
                <Select name="plan_code" defaultValue={overview.plans.find((p) => p.is_active)?.code}>
                  <SelectTrigger className="bg-surface-2" aria-label={a.create.plan}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {overview.plans
                      .filter((p) => p.is_active)
                      .map((p) => (
                        <SelectItem key={p.code} value={p.code}>
                          {p.name}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex justify-end">
              <Button type="submit" disabled={busy}>
                {busy && <Loader2 className="size-4 animate-spin" />}
                {a.create.submit}
              </Button>
            </div>
          </form>
        </TabsContent>

        <TabsContent value="studios" className="mt-5 space-y-3">
          <p className="text-xs text-muted-foreground">{a.studios.hint}</p>
          <div className="panel divide-y divide-border">
            {overview.workspaces.map((ws) => (
              <div key={ws.id} className="space-y-3 p-4 text-sm">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium">{ws.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {owner(ws.owner_id)} · {t.common.credits(formatNumber(ws.credits))}
                      {ws.ends_at ? ` · ${a.studios.expires(formatDate(ws.ends_at))}` : ""}
                    </p>
                  </div>
                  <StatusBadge status={ws.status} label={subscription(ws.status)} />
                  <Select
                    value={ws.plan_code ?? undefined}
                    disabled={busy}
                    onValueChange={(code) =>
                      setConfirm({
                        title: a.studios.changePlanTitle,
                        description: a.studios.changePlanDescription(ws.name, ws.plan_code ?? "—", code),
                        run: () =>
                          api(
                            `admin/workspaces/${ws.id}/subscription`,
                            jsonRequest("PUT", { plan_code: code, status: ws.status === "active" ? "active" : "paused" }),
                          ),
                      })
                    }
                  >
                    <SelectTrigger className="h-8 w-36 bg-surface-2" aria-label={a.create.plan}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {overview.plans
                        .filter((p) => p.is_active || p.code === ws.plan_code)
                        .map((p) => (
                          <SelectItem key={p.code} value={p.code}>
                            {p.name}
                          </SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      setConfirm({
                        title: ws.status === "active" ? a.studios.pauseTitle : a.studios.activateTitle,
                        description:
                          ws.status === "active" ? a.studios.pauseDescription(ws.name) : a.studios.activateDescription(ws.name),
                        run: () =>
                          api(
                            `admin/workspaces/${ws.id}/subscription`,
                            jsonRequest("PUT", { plan_code: ws.plan_code, status: ws.status === "active" ? "paused" : "active" }),
                          ),
                      })
                    }
                  >
                    {ws.status === "active" ? a.studios.pause : a.studios.activate}
                  </Button>
                </div>
                <form
                  className="flex flex-wrap gap-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    const form = event.currentTarget;
                    const values = new FormData(form);
                    void mutate(() =>
                      api(
                        `admin/workspaces/${ws.id}/credits`,
                        jsonRequest("POST", { delta: Number(values.get("delta")), reason: values.get("reason") }),
                      ),
                    ).then((ok) => ok && form.reset());
                  }}
                >
                  <Input
                    name="delta"
                    type="number"
                    min={-1000000}
                    max={1000000}
                    required
                    placeholder={a.studios.delta}
                    aria-label={`${a.studios.delta} · ${ws.name}`}
                    className="h-8 w-32 bg-surface-2"
                  />
                  <Input
                    name="reason"
                    minLength={3}
                    maxLength={80}
                    required
                    placeholder={a.studios.reason}
                    aria-label={`${a.studios.reason} · ${ws.name}`}
                    className="h-8 min-w-[160px] flex-1 bg-surface-2"
                  />
                  <Button type="submit" size="sm" variant="outline" disabled={busy}>
                    {a.studios.adjust}
                  </Button>
                </form>
              </div>
            ))}
          </div>
        </TabsContent>

        <TabsContent value="plans" className="mt-5 space-y-4">
          <p className="text-xs text-muted-foreground">{a.plans.hint}</p>
          <div className="grid gap-4 lg:grid-cols-2">
            {overview.plans.map((plan) => (
              <PlanForm
                key={`${plan.code}-${plan.name}-${plan.price_vnd}-${plan.is_active}`}
                plan={plan}
                busy={busy}
                onSave={(body) => void mutate(() => api(`admin/plans/${plan.code}`, jsonRequest("PUT", body)))}
              />
            ))}
          </div>
        </TabsContent>
      </Tabs>

      <AlertDialog open={Boolean(confirm)} onOpenChange={(open) => !open && !busy && setConfirm(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirm?.title}</AlertDialogTitle>
            <AlertDialogDescription>{confirm?.description}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction
              disabled={busy}
              onClick={(e) => {
                e.preventDefault();
                if (confirm) void mutate(confirm.run);
              }}
            >
              {busy && <Loader2 className="size-4 animate-spin" />}
              {t.common.confirm}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
