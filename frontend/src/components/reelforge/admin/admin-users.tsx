"use client";

import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, MoreHorizontal, Plus, Search } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, jsonRequest } from "@/lib/api";
import { errorText, useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { keys, useAdminUsers } from "@/lib/queries";
import type { AdminUser, AdminUserDetail, Plan } from "@/lib/types";
import { DataTable, useDebounced, type Column } from "../data-table";
import { FieldLabel, StatusBadge } from "../primitives";
import { ConfirmDialog, Detail, FilterSelect, type Confirm } from "./shared";

const LIMIT = 20;

function CreateAccountDialog({ open, plans, onClose }: { open: boolean; plans: Plan[]; onClose: () => void }) {
  const { t } = useI18n();
  const a = t.admin.create;
  const client = useQueryClient();
  const showError = useErrorToast();
  const [busy, setBusy] = useState(false);
  const active = plans.filter((plan) => plan.is_active);
  const [plan, setPlan] = useState(active[0]?.code ?? "trial");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    setBusy(true);
    try {
      await api("admin/accounts", jsonRequest("POST", { ...values, plan_code: plan }));
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.adminUsers }),
        client.invalidateQueries({ queryKey: keys.adminWorkspaces }),
        client.invalidateQueries({ queryKey: keys.admin }),
      ]);
      toast.success(a.created);
      form.reset();
      onClose();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && !busy && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{a.title}</DialogTitle>
          <DialogDescription>{a.hint}</DialogDescription>
        </DialogHeader>
        <form id="create-account" onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <FieldLabel htmlFor="new-email">{a.email}</FieldLabel>
            <Input id="new-email" type="email" name="email" required autoComplete="off" className="bg-surface" />
          </div>
          <div>
            <FieldLabel htmlFor="new-password">{a.password}</FieldLabel>
            <Input id="new-password" type="password" name="password" minLength={12} required autoComplete="new-password"
                   className="bg-surface" />
          </div>
          <div>
            <FieldLabel htmlFor="new-studio">{a.studio}</FieldLabel>
            <Input id="new-studio" name="workspace_name" maxLength={100} required className="bg-surface" />
          </div>
          <div className="sm:col-span-2">
            <FieldLabel>{a.plan}</FieldLabel>
            <Select value={plan} onValueChange={setPlan}>
              <SelectTrigger className="bg-surface" aria-label={a.plan}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {active.map((item) => (
                  <SelectItem key={item.code} value={item.code}>
                    {item.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t.common.cancel}
          </Button>
          <Button type="submit" form="create-account" disabled={busy}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {a.submit}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Phase 22: what an admin can do for a user's account security; each action is audited. */
function UserSecurityActions({ user }: { user: AdminUserDetail }) {
  const { t } = useI18n();
  const a = t.adminV1.users;
  const client = useQueryClient();
  const showError = useErrorToast();
  const [busy, setBusy] = useState<string | null>(null);

  async function run(action: string, done: (result: Record<string, number | boolean>) => string) {
    setBusy(action);
    try {
      const result = await api<Record<string, number | boolean>>(`admin/users/${encodeURIComponent(user.id)}/${action}`, { method: "POST" });
      toast.success(done(result));
      await client.invalidateQueries({ queryKey: keys.adminUsers });
    } catch (error) {
      showError(error);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-wrap gap-2 border-t border-border pt-3">
      {user.two_factor && (
        <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void run("reset-2fa", () => a.reset2faDone)}>
          {busy === "reset-2fa" && <Loader2 className="size-3.5 animate-spin" />}{a.reset2fa}
        </Button>
      )}
      {user.email_verified === false && (
        <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void run("verify-email", () => a.verifyDone)}>
          {busy === "verify-email" && <Loader2 className="size-3.5 animate-spin" />}{a.verifyEmail}
        </Button>
      )}
      <Button size="sm" variant="outline" disabled={busy !== null || !user.active_sessions}
              onClick={() => void run("revoke-sessions", (result) => a.revokeDone(Number(result.revoked ?? 0)))}>
        {busy === "revoke-sessions" && <Loader2 className="size-3.5 animate-spin" />}{a.revokeSessions}
      </Button>
    </div>
  );
}

function UserDetailDialog({ userId, onClose }: { userId: string | null; onClose: () => void }) {
  const { t, formatDateTime, formatNumber } = useI18n();
  const u = t.admin.users;
  const detail = useQuery({
    queryKey: [...keys.adminUsers, "detail", userId],
    queryFn: () => api<AdminUserDetail>(`admin/users/${encodeURIComponent(userId!)}`),
    enabled: Boolean(userId),
  });
  const data = detail.data;
  return (
    <Dialog open={Boolean(userId)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="break-all">{data?.email ?? u.detailTitle}</DialogTitle>
          <DialogDescription>{u.detailTitle}</DialogDescription>
        </DialogHeader>
        {!data ? (
          detail.isError ? (
            <p className="text-sm text-destructive">{errorText(detail.error, t)}</p>
          ) : (
            <Loader2 className="mx-auto my-6 size-5 animate-spin text-muted-foreground" />
          )
        ) : (
          <div className="space-y-4">
            <dl className="grid gap-3 sm:grid-cols-2">
              <Detail label={u.columns.role}>{data.is_admin ? u.admin : u.member}</Detail>
              <Detail label={u.columns.status}>{data.is_active ? u.active : u.locked}</Detail>
              <Detail label={u.columns.created}>{data.created_at ? formatDateTime(data.created_at) : "—"}</Detail>
              <Detail label={u.sessionsLabel}>{formatNumber(data.active_sessions)}</Detail>
              {data.display_name && <Detail label={u.displayName}>{data.display_name}</Detail>}
              {data.email_verified !== undefined && (
                <Detail label={t.account.email.title}>{data.email_verified ? t.adminV1.users.verified : t.adminV1.users.unverified}</Detail>
              )}
              {data.two_factor !== undefined && (
                <Detail label={t.adminV1.users.twoFactor}>{data.two_factor ? t.account.twoFactor.on : t.account.twoFactor.off}</Detail>
              )}
              {data.last_login_at && <Detail label={t.adminV1.users.lastLogin}>{formatDateTime(data.last_login_at)}</Detail>}
            </dl>
            <UserSecurityActions user={data} />
            <div>
              <p className="mb-2 text-xs font-medium text-muted-foreground">{u.studios}</p>
              <ul className="divide-y divide-border rounded-lg border border-border text-sm">
                {data.workspaces.map((ws) => (
                  <li key={ws.id} className="flex items-center justify-between gap-3 px-3 py-2">
                    <span className="min-w-0 truncate">
                      {ws.name} <span className="text-xs text-muted-foreground">· {ws.role}</span>
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {(ws.plan_code ?? "—").toUpperCase()} · {t.common.credits(formatNumber(ws.credits))}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Users, searched and filtered on the server, one page at a time; accounts are created in a dialog. */
export function AdminUsers({ plans, selfEmail }: { plans: Plan[]; selfEmail: string | undefined }) {
  const { t, formatDate } = useI18n();
  const u = t.admin.users;
  const client = useQueryClient();
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  const [viewing, setViewing] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const search = useDebounced(q.trim());
  const users = useAdminUsers({ q: search, role, status, limit: LIMIT, offset });
  const filtered = (setter: (value: string) => void) => (value: string) => {
    setter(value);
    setOffset(0);
  };

  const columns: Column<AdminUser>[] = [
    { key: "email", header: u.columns.email, className: "max-w-[260px] 2xl:max-w-[380px]",
      cell: (user) => (
        <div className="min-w-0">
          <p className="truncate font-medium" title={user.email}>{user.email}</p>
          {user.display_name && <p className="truncate text-xs text-muted-foreground">{user.display_name}</p>}
        </div>
      ) },
    { key: "role", header: u.columns.role, cell: (user) => (user.is_admin ? u.admin : u.member) },
    { key: "studio", header: u.columns.studio, className: "max-w-[200px] 2xl:max-w-[300px]",
      cell: (user) => <span className="block truncate" title={user.workspace?.name}>{user.workspace?.name ?? "—"}</span> },
    { key: "plan", header: u.columns.plan, cell: (user) => (user.plan_code ?? "—").toUpperCase() },
    { key: "status", header: u.columns.status, className: "whitespace-nowrap",
      cell: (user) => (
        <StatusBadge status={user.is_active ? "connected" : "failed"} label={user.is_active ? u.active : u.locked} />
      ) },
    { key: "created", header: u.columns.created, className: "whitespace-nowrap text-muted-foreground",
      cell: (user) => (user.created_at ? formatDate(user.created_at) : "—") },
    { key: "actions", header: <span className="sr-only">{u.columns.actions}</span>, className: "w-10 text-right",
      cell: (user) => (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="size-7" aria-label={`${u.columns.actions} · ${user.email}`}>
              <MoreHorizontal className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => setViewing(user.id)}>{u.view}</DropdownMenuItem>
            <DropdownMenuItem
              disabled={user.email === selfEmail}
              className={user.is_active ? "text-destructive" : undefined}
              onClick={() =>
                setConfirm({
                  title: user.is_active ? u.lockTitle : u.unlockTitle,
                  description: user.is_active ? u.lockDescription(user.email) : u.unlockDescription(user.email),
                  run: () => api(`admin/users/${user.id}`, jsonRequest("PUT", { is_active: !user.is_active })),
                  done: () => client.invalidateQueries({ queryKey: keys.adminUsers }),
                })
              }
            >
              {user.is_active ? u.lock : u.unlock}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ) },
  ];

  return (
    <>
      <DataTable
        columns={columns}
        rows={users.data?.items ?? []}
        rowKey={(user) => user.id}
        loading={users.isFetching}
        error={users.isError ? errorText(users.error, t) : null}
        empty={u.empty}
        total={users.data?.total ?? 0}
        limit={LIMIT}
        offset={offset}
        onOffset={setOffset}
        toolbar={
          <>
            <div className="relative min-w-[200px] flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setOffset(0);
                }}
                placeholder={u.searchPlaceholder}
                aria-label={u.searchPlaceholder}
                className="h-8 bg-surface pl-8"
              />
            </div>
            <FilterSelect value={role} onChange={filtered(setRole)} all={u.allRoles}
                          options={[["admin", u.admin], ["member", u.member]]} />
            <FilterSelect value={status} onChange={filtered(setStatus)} all={u.allStatuses}
                          options={[["active", u.active], ["locked", u.locked]]} />
            <Button size="sm" className="h-8" onClick={() => setCreating(true)}>
              <Plus className="size-4" /> {u.createAccount}
            </Button>
          </>
        }
      />
      <CreateAccountDialog open={creating} plans={plans} onClose={() => setCreating(false)} />
      <UserDetailDialog userId={viewing} onClose={() => setViewing(null)} />
      <ConfirmDialog confirm={confirm} onClose={() => setConfirm(null)} />
    </>
  );
}
