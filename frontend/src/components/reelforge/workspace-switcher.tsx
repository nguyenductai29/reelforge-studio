"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Building2, Check, ChevronsUpDown, Loader2, Plus, ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api, jsonRequest } from "@/lib/api";
import { errorText, useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { keys, useDashboard } from "@/lib/queries";
import { FieldLabel } from "./primitives";
import { FormError } from "./public-shell";

/** Phase 23: the studios the user belongs to; switching changes the workspace of this session. */
export function WorkspaceSwitcher() {
  const { t } = useI18n();
  const w = t.team.switcher;
  const router = useRouter();
  const client = useQueryClient();
  const showError = useErrorToast();
  const { data } = useDashboard();
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!data) return null;
  const role = data.workspace.role;
  const workspaces = data.workspaces ?? [{ id: data.workspace.id, name: data.workspace.name, role: role ?? "owner", active: true }];

  async function switchTo(id: string, name: string) {
    try {
      await api(`workspaces/${encodeURIComponent(id)}/switch`, { method: "POST" });
      toast.success(w.switched(name));
      await client.resetQueries();
      router.push("/");
    } catch (e) {
      showError(e);
    }
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("workspaces", jsonRequest("POST", { name: new FormData(event.currentTarget).get("name") }));
      toast.success(w.created);
      setCreating(false);
      await client.resetQueries();
      router.push("/");
    } catch (e) {
      setError(errorText(e, t));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="sm" className="max-w-[14rem] gap-1.5 px-2" aria-label={w.label}>
            <Building2 className="size-4 text-muted-foreground" aria-hidden />
            <span className="truncate">{data.workspace.name}</span>
            {role && <span className="hidden text-xs text-muted-foreground sm:inline">· {t.team.roles[role]}</span>}
            <ChevronsUpDown className="size-3.5 text-muted-foreground" aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-64">
          <DropdownMenuLabel>{w.label}</DropdownMenuLabel>
          {workspaces.map((item) => (
            <DropdownMenuItem key={item.id} onSelect={() => !item.active && void switchTo(item.id, item.name)}>
              <Check className={item.active ? "size-4" : "size-4 opacity-0"} aria-hidden />
              <span className="min-w-0 flex-1 truncate">{item.name}</span>
              <span className="text-xs text-muted-foreground">{t.team.roles[item.role]}</span>
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setCreating(true)}>
            <Plus className="size-4" aria-hidden />
            {w.create}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={creating} onOpenChange={(open) => !busy && setCreating(open)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{w.create}</DialogTitle>
            <DialogDescription>{t.auth.registerSubtitle}</DialogDescription>
          </DialogHeader>
          <form onSubmit={create} className="space-y-3">
            <div>
              <FieldLabel htmlFor="new-studio">{w.createName}</FieldLabel>
              <Input id="new-studio" name="name" required maxLength={100} className="bg-surface-2" />
            </div>
            <FormError message={error} />
            <Button type="submit" disabled={busy}>{busy && <Loader2 className="size-4 animate-spin" />}{w.create}</Button>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}

/** Account notices: email to verify, terms to accept, an administrator without 2FA. */
export function AccountBanners() {
  const { t } = useI18n();
  const b = t.account.banners;
  const client = useQueryClient();
  const showError = useErrorToast();
  const { data } = useDashboard();
  const [sent, setSent] = useState(false);
  const account = data?.account;
  if (!account) return null;
  const notices = [];
  if (!account.email_verified && account.email_delivery) {
    notices.push(
      <div key="verify" className="flex flex-wrap items-center justify-between gap-2">
        <span>{sent ? t.account.email.resent : b.verifyEmail}</span>
        {!sent && (
          <Button size="sm" variant="outline" onClick={() => void api("account/verify-email/resend", { method: "POST" })
            .then(() => setSent(true)).catch(showError)}>{b.verifyAction}</Button>
        )}
      </div>,
    );
  }
  if (!account.terms_accepted) {
    notices.push(
      <div key="terms" className="flex flex-wrap items-center justify-between gap-2">
        <span>{b.terms} <Link href="/terms" target="_blank" className="underline underline-offset-2">{t.legal.links.terms}</Link>
          {" · "}<Link href="/privacy" target="_blank" className="underline underline-offset-2">{t.legal.links.privacy}</Link></span>
        <Button size="sm" variant="outline" onClick={() => void api("account/terms", { method: "POST" })
          .then(() => client.invalidateQueries({ queryKey: keys.dashboard })).catch(showError)}>{t.account.terms.accept}</Button>
      </div>,
    );
  }
  if (data?.workspace.role === "viewer") {
    notices.push(<div key="viewer">{t.team.viewOnly}</div>);
  }
  if (data?.is_admin && !account.two_factor_enabled) {
    notices.push(
      <div key="2fa" className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2"><ShieldAlert className="size-4" aria-hidden />{b.admin2fa}</span>
        <Button size="sm" variant="outline" asChild><Link href="/settings?tab=security">{b.admin2faAction}</Link></Button>
      </div>,
    );
  }
  if (!notices.length) return null;
  return (
    <div role="status" className="mb-4 space-y-2 rounded-lg border border-warning/40 bg-warning/10 px-4 py-3 text-sm">
      {notices}
    </div>
  );
}
