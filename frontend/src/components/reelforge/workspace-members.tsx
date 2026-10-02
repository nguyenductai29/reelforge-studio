"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Copy, Crown, Loader2, LogOut, Mail, MoreHorizontal, Plus, Search, ShieldCheck } from "lucide-react";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, jsonRequest } from "@/lib/api";
import { errorText, useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { can } from "@/lib/permissions";
import { keys, useDashboard, useMemberPage } from "@/lib/queries";
import type { InviteResult, MemberRow, Role } from "@/lib/types";
import { ConfirmDialog, FilterSelect, type Confirm } from "./admin/shared";
import { DataTable, useDebounced, type Column } from "./data-table";
import { FieldLabel, StatusBadge } from "./primitives";
import { FormError } from "./public-shell";

const LIMIT = 20;
const INVITABLE: Exclude<Role, "owner">[] = ["admin", "editor", "viewer"];
type ActiveMember = Extract<MemberRow, { kind: "member" }>;

/** Owners manage everyone but themselves (ownership moves by transfer); admins manage everyone but the owner. The
 *  API applies the same rule to every request; this only hides what it would refuse. */
function manageable(actor: Role | undefined, target: Role) {
  return actor === "owner" ? target !== "owner" : actor === "admin" ? target !== "owner" : false;
}

/** A link to send by hand, when the server sends no email. */
function ShareLink({ link }: { link: string }) {
  const { t } = useI18n();
  const m = t.team;
  return (
    <div className="space-y-2 rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">
      <p>{m.shareLink}</p>
      <div className="flex gap-2">
        <Input readOnly value={link} onFocus={(e) => e.currentTarget.select()} aria-label={m.copyLink}
               className="h-8 bg-surface-2 font-mono text-xs" />
        <Button size="sm" variant="outline" onClick={() => void navigator.clipboard?.writeText(link)}>
          <Copy className="size-3.5" />{m.copyLink}
        </Button>
      </div>
    </div>
  );
}

function InviteDialog({ open, mustVerify, onClose, refresh }: {
  open: boolean;
  mustVerify: boolean;
  onClose: () => void;
  refresh: () => Promise<unknown>;
}) {
  const { t } = useI18n();
  const m = t.team;
  const [role, setRole] = useState<Exclude<Role, "owner">>("editor");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [link, setLink] = useState<string | null>(null);

  function close() {
    if (busy) return;
    setError("");
    setLink(null);
    onClose();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    setBusy(true);
    setError("");
    try {
      const result = await api<InviteResult>("workspace/invites", jsonRequest("POST", {
        email: new FormData(form).get("email"), role,
      }));
      await refresh();
      if (result.link) {
        setLink(result.link);  // no email delivery: the dialog shows the link to send by hand
      } else {
        toast.success(m.invited(result.email));
        form.reset();
        onClose();
      }
    } catch (e) {
      setError(errorText(e, t));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && close()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{m.inviteMember}</DialogTitle>
          <DialogDescription>{m.inviteHint}</DialogDescription>
        </DialogHeader>
        {mustVerify ? (
          <p className="text-sm text-warning">{m.verifyFirst}</p>
        ) : link ? (
          <ShareLink link={link} />
        ) : (
          <form id="invite-member" className="space-y-4" onSubmit={submit}>
            <div>
              <FieldLabel htmlFor="invite-email">{m.inviteEmail}</FieldLabel>
              <Input id="invite-email" name="email" type="email" required autoComplete="off" className="bg-surface" />
            </div>
            <div>
              <FieldLabel htmlFor="invite-role">{m.columns.role}</FieldLabel>
              <Select value={role} onValueChange={(value) => setRole(value as Exclude<Role, "owner">)}>
                <SelectTrigger id="invite-role" className="bg-surface"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {INVITABLE.map((item) => <SelectItem key={item} value={item}>{m.roles[item]}</SelectItem>)}
                </SelectContent>
              </Select>
              <p className="mt-1.5 text-xs text-muted-foreground">{m.roleHints[role]}</p>
            </div>
            <FormError message={error} />
          </form>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {mustVerify || link ? t.common.close : t.common.cancel}
          </Button>
          {!mustVerify && !link && (
            <Button type="submit" form="invite-member" disabled={busy}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : <Mail className="size-4" />}
              {m.send}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function TransferDialog({ target, studio, onClose, refresh }: {
  target: ActiveMember | null;
  studio: string;
  onClose: () => void;
  refresh: () => Promise<unknown>;
}) {
  const { t } = useI18n();
  const m = t.team;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function close() {
    if (busy) return;
    setError("");
    onClose();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!target) return;
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      await api("workspace/transfer", jsonRequest("POST", {
        user_id: target.user_id, password: form.get("password"), code: form.get("code") || null, confirm: form.get("confirm"),
      }));
      toast.success(m.transferred);
      await refresh();
      onClose();
    } catch (e) {
      setError(errorText(e, t));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={Boolean(target)} onOpenChange={(next) => !next && close()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{m.transfer}</DialogTitle>
          <DialogDescription>{m.transferHint}</DialogDescription>
        </DialogHeader>
        <form id="transfer-ownership" className="space-y-4" onSubmit={submit}>
          <div>
            <FieldLabel>{m.transferTarget}</FieldLabel>
            <p className="break-all text-sm">{target?.display_name ? `${target.display_name} · ${target.email}` : target?.email}</p>
          </div>
          <div>
            <FieldLabel htmlFor="transfer-confirm">{m.confirmName(studio)}</FieldLabel>
            <Input id="transfer-confirm" name="confirm" required autoComplete="off" className="bg-surface" />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <FieldLabel htmlFor="transfer-password">{m.password}</FieldLabel>
              <Input id="transfer-password" name="password" type="password" autoComplete="current-password" required
                     className="bg-surface" />
            </div>
            <div>
              <FieldLabel htmlFor="transfer-code">{m.code}</FieldLabel>
              <Input id="transfer-code" name="code" autoComplete="one-time-code" maxLength={20} className="bg-surface font-mono" />
            </div>
          </div>
          <FormError message={error} />
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={close} disabled={busy}>{t.common.cancel}</Button>
          <Button type="submit" form="transfer-ownership" variant="destructive" disabled={busy}>
            {busy && <Loader2 className="size-4 animate-spin" />}{m.transfer}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Settings → Members: a table that scales to studios of any size. Search, filters and pages come from the server
 * (20 rows a page); the toolbar and the pagination stay put while the rows scroll. Pending invitations are rows
 * too, for those who manage members. Every action shown is one the API allows the viewer; the API decides anyway.
 */
export function WorkspaceMembersPanel() {
  const { t, formatDate, formatDateTime } = useI18n();
  const m = t.team;
  const router = useRouter();
  const client = useQueryClient();
  const showError = useErrorToast();
  const { data: dashboard } = useDashboard();
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [inviting, setInviting] = useState(false);
  const [sharedLink, setSharedLink] = useState<string | null>(null);
  const [transferTo, setTransferTo] = useState<ActiveMember | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const search = useDebounced(q.trim());
  const page = useMemberPage({ q: search, role, status, limit: LIMIT, offset });

  const myRole = dashboard?.workspace.role;
  const canManage = can(dashboard, "members.manage");
  const canTransfer = can(dashboard, "ownership.transfer");
  const mustVerify = Boolean(dashboard?.account?.email_delivery && dashboard.account.email_verified === false);
  const filtered = (setter: (value: string) => void) => (value: string) => {
    setter(value);
    setOffset(0);
  };

  async function refresh() {
    await Promise.all([client.invalidateQueries({ queryKey: keys.members }),
                       client.invalidateQueries({ queryKey: keys.dashboard })]);
  }

  async function changeRole(member: ActiveMember, next: string) {
    if (next === member.role) return;
    try {
      await api(`workspace/members/${encodeURIComponent(member.user_id)}`, jsonRequest("PUT", { role: next }));
      toast.success(m.roleChanged);
      await refresh();
    } catch (error) {
      showError(error);
    }
  }

  async function resend(invite: Extract<MemberRow, { kind: "invite" }>) {
    try {
      const result = await api<InviteResult>(`workspace/invites/${encodeURIComponent(invite.id)}/resend`, { method: "POST" });
      if (result.link) setSharedLink(result.link);
      else toast.success(m.invited(invite.email));
      await refresh();
    } catch (error) {
      showError(error);
    }
  }

  function actions(row: MemberRow) {
    if (row.kind === "invite") {
      if (!canManage) return null;
      return (
        <>
          <DropdownMenuItem onClick={() => void resend(row)}>{m.resendInvitation}</DropdownMenuItem>
          <DropdownMenuItem className="text-destructive" onClick={() => setConfirm({
            title: m.revoke, description: row.email,
            run: () => api(`workspace/invites/${encodeURIComponent(row.id)}`, { method: "DELETE" }), done: refresh,
          })}>{m.revoke}</DropdownMenuItem>
        </>
      );
    }
    const manage = canManage && !row.you && manageable(myRole, row.role);
    const transfer = canTransfer && !row.you && row.role !== "owner";
    if (!manage && !transfer) return null;
    return (
      <>
        {manage && (
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>{m.changeRole}</DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
              <DropdownMenuRadioGroup value={row.role} onValueChange={(next) => void changeRole(row, next)}>
                {INVITABLE.map((item) => <DropdownMenuRadioItem key={item} value={item}>{m.roles[item]}</DropdownMenuRadioItem>)}
              </DropdownMenuRadioGroup>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        )}
        {transfer && <DropdownMenuItem onClick={() => setTransferTo(row)}>{m.transfer}</DropdownMenuItem>}
        {manage && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="text-destructive" onClick={() => setConfirm({
              title: m.removeMember, description: m.removeConfirm(row.email),
              run: () => api(`workspace/members/${encodeURIComponent(row.user_id)}`, { method: "DELETE" }), done: refresh,
            })}>{m.removeMember}</DropdownMenuItem>
          </>
        )}
      </>
    );
  }

  const columns: Column<MemberRow>[] = [
    { key: "member", header: m.columns.member, className: "max-w-[320px] 2xl:max-w-[480px]",
      cell: (row) => (
        <div className="min-w-0">
          <p className="flex min-w-0 items-center gap-1.5">
            {row.kind === "member" && row.role === "owner" && <Crown className="size-3.5 shrink-0 text-warning" aria-label={m.roles.owner} />}
            <span className="truncate font-medium" title={row.display_name || row.email}>{row.display_name || row.email}</span>
            {row.kind === "member" && row.you && (
              <span className="shrink-0 rounded bg-primary/15 px-1.5 text-[11px] text-primary">{m.you}</span>
            )}
            {row.kind === "member" && row.two_factor && (
              <ShieldCheck className="size-3.5 shrink-0 text-success" aria-label={m.twoFactor} />
            )}
          </p>
          {row.display_name && <p className="truncate text-xs text-muted-foreground" title={row.email}>{row.email}</p>}
        </div>
      ) },
    { key: "role", header: m.columns.role, className: "whitespace-nowrap", cell: (row) => m.roles[row.role] },
    { key: "status", header: m.columns.status, className: "whitespace-nowrap",
      cell: (row) => row.kind === "member"
        ? <StatusBadge status="connected" label={m.statusActive} />
        : <StatusBadge status={row.expired ? "needs attention" : "scheduled"} label={m.statusPending} /> },
    { key: "joined", header: m.columns.joined, className: "whitespace-nowrap text-xs text-muted-foreground",
      cell: (row) => row.kind === "member" ? (row.joined_at ? formatDate(row.joined_at) : "—") : (
        <span title={row.invited_by ? m.invitedBy(row.invited_by) : undefined}>
          {m.invitedOn(formatDate(row.invited_at))}
          <span className={`block ${row.expired ? "text-warning" : ""}`}>
            {row.expired ? m.expired : m.expires(formatDateTime(row.expires_at))}
          </span>
        </span>
      ) },
    { key: "actions", header: <span className="sr-only">{m.columns.actions}</span>, className: "w-10 text-right",
      cell: (row) => {
        const items = actions(row);
        return items && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="size-7" aria-label={`${m.columns.actions} · ${row.email}`}>
                <MoreHorizontal className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">{items}</DropdownMenuContent>
          </DropdownMenu>
        );
      } },
  ];

  return (
    <>
      <DataTable
        columns={columns}
        rows={page.data?.items ?? []}
        rowKey={(row) => `${row.kind}:${row.id}`}
        loading={page.isFetching}
        error={page.isError ? errorText(page.error, t) : null}
        empty={m.noMatches}
        total={page.data?.total ?? 0}
        limit={LIMIT}
        offset={offset}
        onOffset={setOffset}
        minWidth={680}
        toolbar={
          <>
            <div className="relative min-w-[180px] flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setOffset(0);
                }}
                type="search"
                placeholder={m.searchMembers}
                aria-label={m.searchMembers}
                className="h-8 bg-surface pl-8"
              />
            </div>
            <FilterSelect value={role} onChange={filtered(setRole)} all={m.allRoles}
                          options={(["owner", ...INVITABLE] as Role[]).map((item) => [item, m.roles[item]])} />
            {canManage && (
              <FilterSelect value={status} onChange={filtered(setStatus)} all={m.allStatuses}
                            options={[["active", m.statusActive], ["pending", m.statusPending]]} />
            )}
            {canManage && (
              <Button size="sm" className="h-8" onClick={() => setInviting(true)}>
                <Plus className="size-4" /> {m.inviteMember}
              </Button>
            )}
            {myRole && myRole !== "owner" && (
              <Button size="sm" variant="outline" className="h-8" onClick={() => setConfirm({
                title: m.leave, description: m.leaveConfirm,
                run: () => api("workspace/leave", { method: "POST" }),
                done: async () => {
                  toast.success(m.left);
                  await client.resetQueries();
                  router.push("/");
                },
              })}>
                <LogOut className="size-3.5" />{m.leave}
              </Button>
            )}
          </>
        }
      />
      <InviteDialog open={inviting} mustVerify={mustVerify} onClose={() => setInviting(false)} refresh={refresh} />
      <TransferDialog target={transferTo} studio={dashboard?.workspace.name ?? ""} onClose={() => setTransferTo(null)}
                      refresh={refresh} />
      <Dialog open={Boolean(sharedLink)} onOpenChange={(open) => !open && setSharedLink(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{m.resendInvitation}</DialogTitle>
            <DialogDescription>{m.inviteHint}</DialogDescription>
          </DialogHeader>
          {sharedLink && <ShareLink link={sharedLink} />}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setSharedLink(null)}>{t.common.close}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ConfirmDialog confirm={confirm} onClose={() => setConfirm(null)} />
    </>
  );
}
