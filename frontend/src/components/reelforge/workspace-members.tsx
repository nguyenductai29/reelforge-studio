"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Copy, Crown, Loader2, LogOut, Mail, ShieldCheck, Trash2, UserPlus } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, jsonRequest } from "@/lib/api";
import { errorText, useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { keys, useMembers, useSettings } from "@/lib/queries";
import type { InviteResult, Member, Role } from "@/lib/types";
import { ConfirmDialog, type Confirm } from "./admin/shared";
import { FieldLabel, SettingRow } from "./primitives";
import { FormError } from "./public-shell";

const INVITABLE: Exclude<Role, "owner">[] = ["admin", "editor", "viewer"];

function manageable(actor: Role, target: Role) {
  return actor === "owner" ? target !== "owner" : actor === "admin" ? target !== "owner" : false;
}

export function WorkspaceMembersPanel() {
  const { t, formatDate, formatDateTime } = useI18n();
  const m = t.team;
  const router = useRouter();
  const client = useQueryClient();
  const showError = useErrorToast();
  const members = useMembers();
  const settings = useSettings();
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [inviteRole, setInviteRole] = useState<Exclude<Role, "owner">>("editor");
  const [inviteBusy, setInviteBusy] = useState(false);
  const [inviteError, setInviteError] = useState("");
  const [shared, setShared] = useState<string | null>(null);
  const [transferBusy, setTransferBusy] = useState(false);
  const [transferError, setTransferError] = useState("");
  const [transferTarget, setTransferTarget] = useState("");

  if (members.isPending) return <Loader2 className="size-4 animate-spin text-muted-foreground" />;
  if (members.isError) return <p role="alert" className="text-sm text-destructive">{errorText(members.error, t)}</p>;
  const view = members.data;
  const others = view.members.filter((member) => !member.you);

  async function refresh() {
    await Promise.all([client.invalidateQueries({ queryKey: keys.members }),
                       client.invalidateQueries({ queryKey: keys.dashboard })]);
  }

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    setInviteBusy(true);
    setInviteError("");
    setShared(null);
    try {
      const result = await api<InviteResult>("workspace/invites", jsonRequest("POST", {
        email: new FormData(formElement).get("email"), role: inviteRole,
      }));
      if (result.link) setShared(result.link);
      else toast.success(m.invited(result.email));
      formElement.reset();
      await refresh();
    } catch (e) {
      setInviteError(errorText(e, t));
    } finally {
      setInviteBusy(false);
    }
  }

  async function changeRole(member: Member, role: string) {
    try {
      await api(`workspace/members/${encodeURIComponent(member.user_id)}`, jsonRequest("PUT", { role }));
      toast.success(m.roleChanged);
      await refresh();
    } catch (error) {
      showError(error);
    }
  }

  async function rename(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await api("workspace", jsonRequest("PUT", { name: new FormData(event.currentTarget).get("name") }));
      toast.success(m.renamed);
      await refresh();
    } catch (error) {
      showError(error);
    }
  }

  async function setEditorsPublish(value: boolean) {
    if (!settings.data) return;
    try {
      await api("settings/workspace", jsonRequest("PUT", { ...settings.data.workspace, editors_can_publish: value }));
      await Promise.all([refresh(), client.invalidateQueries({ queryKey: keys.settings })]);
    } catch (error) {
      showError(error);
    }
  }

  async function transfer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setTransferBusy(true);
    setTransferError("");
    try {
      await api("workspace/transfer", jsonRequest("POST", {
        user_id: transferTarget, password: form.get("password"), code: form.get("code") || null, confirm: form.get("confirm"),
      }));
      toast.success(m.transferred);
      setTransferTarget("");
      await refresh();
    } catch (e) {
      setTransferError(errorText(e, t));
    } finally {
      setTransferBusy(false);
    }
  }

  return (
    <div className="max-w-4xl space-y-4">
      <section className="panel space-y-3 p-5">
        <h3 className="text-sm font-semibold">{m.workspace}</h3>
        {view.can_manage ? (
          <form className="flex flex-wrap items-end gap-2" onSubmit={rename}>
            <div className="min-w-[14rem] flex-1">
              <FieldLabel htmlFor="workspace-name">{t.auth.studioName}</FieldLabel>
              <Input id="workspace-name" name="name" defaultValue={view.workspace.name} required maxLength={100} className="bg-surface-2" />
            </div>
            <Button type="submit" size="sm" variant="outline">{m.rename}</Button>
          </form>
        ) : (
          <p className="text-sm">{view.workspace.name}</p>
        )}
        <p className="text-xs text-muted-foreground">{m.roles[view.role]} · {m.roleHints[view.role]}</p>
        {view.can_manage && (
          <SettingRow label={m.editorsPublish} hint={m.editorsPublishHint}>
            <Switch checked={view.editors_can_publish} onCheckedChange={(value) => void setEditorsPublish(value)}
                    aria-label={m.editorsPublish} />
          </SettingRow>
        )}
      </section>

      <section className="panel p-5" aria-labelledby="members-title">
        <h3 id="members-title" className="mb-3 text-sm font-semibold">{m.title}</h3>
        <div className="relative overflow-x-auto">
          <table className="w-full min-w-[560px] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th scope="col" className="py-2 pr-3 font-medium">{m.columns.member}</th>
                <th scope="col" className="py-2 pr-3 font-medium">{m.columns.role}</th>
                <th scope="col" className="py-2 pr-3 font-medium">{m.columns.joined}</th>
                <th scope="col" className="py-2 font-medium"><span className="sr-only">{m.columns.actions}</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {view.members.map((member) => (
                <tr key={member.user_id}>
                  <td className="py-2.5 pr-3">
                    <p className="flex items-center gap-1.5">
                      {member.role === "owner" && <Crown className="size-3.5 text-warning" aria-hidden />}
                      <span className="truncate">{member.display_name || member.email}</span>
                      {member.you && <span className="rounded bg-primary/15 px-1.5 text-[11px] text-primary">{m.you}</span>}
                      {member.two_factor && <ShieldCheck className="size-3.5 text-success" aria-label={m.twoFactor} />}
                    </p>
                    {member.display_name && <p className="text-xs text-muted-foreground">{member.email}</p>}
                  </td>
                  <td className="py-2.5 pr-3">
                    {view.can_manage && !member.you && manageable(view.role, member.role) ? (
                      <Select value={member.role} onValueChange={(role) => void changeRole(member, role)}>
                        <SelectTrigger className="h-8 w-36 bg-surface" aria-label={m.columns.role}><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {INVITABLE.map((role) => <SelectItem key={role} value={role}>{m.roles[role]}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    ) : m.roles[member.role]}
                  </td>
                  <td className="py-2.5 pr-3 text-xs text-muted-foreground">{member.joined_at ? formatDate(member.joined_at) : "—"}</td>
                  <td className="py-2.5 text-right">
                    {view.can_manage && !member.you && manageable(view.role, member.role) && (
                      <Button size="sm" variant="ghost" aria-label={`${m.remove}: ${member.email}`} onClick={() => setConfirm({
                        title: m.remove, description: m.removeConfirm(member.email),
                        run: () => api(`workspace/members/${encodeURIComponent(member.user_id)}`, { method: "DELETE" }),
                        done: refresh,
                      })}>
                        <Trash2 className="size-3.5" />
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {view.can_manage && (
        <section className="panel space-y-4 p-5">
          <h3 className="flex items-center gap-2 text-sm font-semibold"><UserPlus className="size-4 text-muted-foreground" />{m.invite}</h3>
          {view.email_delivery && !view.email_verified ? (
            <p className="text-sm text-warning">{m.verifyFirst}</p>
          ) : (
            <form className="flex flex-wrap items-end gap-2" onSubmit={invite}>
              <div className="min-w-[14rem] flex-1">
                <FieldLabel htmlFor="invite-email">{m.inviteEmail}</FieldLabel>
                <Input id="invite-email" name="email" type="email" required className="bg-surface-2" />
              </div>
              <div>
                <FieldLabel htmlFor="invite-role">{m.columns.role}</FieldLabel>
                <Select value={inviteRole} onValueChange={(role) => setInviteRole(role as Exclude<Role, "owner">)}>
                  <SelectTrigger id="invite-role" className="h-9 w-36 bg-surface"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {INVITABLE.map((role) => <SelectItem key={role} value={role}>{m.roles[role]}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <Button type="submit" size="sm" disabled={inviteBusy}>
                {inviteBusy ? <Loader2 className="size-3.5 animate-spin" /> : <Mail className="size-3.5" />}{m.send}
              </Button>
            </form>
          )}
          <p className="text-xs text-muted-foreground">{m.roleHints[inviteRole]}</p>
          <FormError message={inviteError} />
          {shared && (
            <div className="space-y-2 rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">
              <p>{m.shareLink}</p>
              <div className="flex gap-2">
                <Input readOnly value={shared} className="h-8 bg-surface-2 font-mono text-xs" aria-label={m.copyLink} />
                <Button size="sm" variant="outline" onClick={() => void navigator.clipboard?.writeText(shared)}>
                  <Copy className="size-3.5" />{m.copyLink}
                </Button>
              </div>
            </div>
          )}
          <div className="border-t border-border pt-3">
            <p className="mb-2 text-xs font-medium text-muted-foreground">{m.pending}</p>
            {view.invites.length === 0 ? <p className="text-sm text-muted-foreground">{m.noInvites}</p> : (
              <ul className="divide-y divide-border text-sm">
                {view.invites.map((item) => (
                  <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                    <span className="min-w-0">
                      <span className="block truncate">{item.email} · {m.roles[item.role]}</span>
                      <span className="text-xs text-muted-foreground">
                        {item.invited_by ? `${m.invitedBy(item.invited_by)} · ` : ""}
                        {item.expired ? m.expired : m.expires(formatDateTime(item.expires_at))}
                      </span>
                    </span>
                    <span className="flex gap-1">
                      <Button size="sm" variant="ghost" onClick={() => void (async () => {
                        try {
                          const result = await api<InviteResult>(`workspace/invites/${encodeURIComponent(item.id)}/resend`, { method: "POST" });
                          if (result.link) setShared(result.link);
                          else toast.success(m.invited(item.email));
                          await refresh();
                        } catch (error) {
                          showError(error);
                        }
                      })()}>{m.resend}</Button>
                      <Button size="sm" variant="ghost" onClick={() => setConfirm({
                        title: m.revoke, description: item.email,
                        run: () => api(`workspace/invites/${encodeURIComponent(item.id)}`, { method: "DELETE" }), done: refresh,
                      })}>{m.revoke}</Button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}

      {view.can_transfer && others.length > 0 && (
        <section className="panel space-y-3 p-5">
          <h3 className="flex items-center gap-2 text-sm font-semibold"><Crown className="size-4 text-muted-foreground" />{m.transfer}</h3>
          <p className="text-xs text-muted-foreground">{m.transferHint}</p>
          <form className="grid gap-3 sm:grid-cols-2" onSubmit={transfer}>
            <div>
              <FieldLabel htmlFor="transfer-target">{m.transferTarget}</FieldLabel>
              <Select value={transferTarget} onValueChange={setTransferTarget}>
                <SelectTrigger id="transfer-target" className="h-9 bg-surface"><SelectValue placeholder="—" /></SelectTrigger>
                <SelectContent>
                  {others.map((member) => <SelectItem key={member.user_id} value={member.user_id}>{member.email}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <FieldLabel htmlFor="transfer-confirm">{m.confirmName(view.workspace.name)}</FieldLabel>
              <Input id="transfer-confirm" name="confirm" required className="bg-surface-2" />
            </div>
            <div>
              <FieldLabel htmlFor="transfer-password">{m.password}</FieldLabel>
              <Input id="transfer-password" name="password" type="password" autoComplete="current-password" required className="bg-surface-2" />
            </div>
            <div>
              <FieldLabel htmlFor="transfer-code">{m.code}</FieldLabel>
              <Input id="transfer-code" name="code" autoComplete="one-time-code" maxLength={20} className="bg-surface-2 font-mono" />
            </div>
            <div className="sm:col-span-2"><FormError message={transferError} /></div>
            <div className="sm:col-span-2">
              <Button type="submit" size="sm" variant="destructive" disabled={transferBusy || !transferTarget}>
                {transferBusy && <Loader2 className="size-3.5 animate-spin" />}{m.transfer}
              </Button>
            </div>
          </form>
        </section>
      )}

      {view.role !== "owner" && (
        <section className="panel flex flex-wrap items-center justify-between gap-3 p-5">
          <p className="text-sm text-muted-foreground">{view.role === "viewer" ? m.viewOnly : m.roleHints[view.role]}</p>
          <Button size="sm" variant="outline" onClick={() => setConfirm({
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
        </section>
      )}
      <ConfirmDialog confirm={confirm} onClose={() => setConfirm(null)} />
    </div>
  );
}
