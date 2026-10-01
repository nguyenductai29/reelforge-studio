"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FieldLabel } from "@/components/reelforge/primitives";
import { FormError, FormNote, PublicShell, TermsCheckbox, TwoFactorStep, useHashToken } from "@/components/reelforge/public-shell";
import { api, ApiError, jsonRequest } from "@/lib/api";
import { errorText } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useAccountSecurity } from "@/lib/queries";
import type { InviteLookup, LoginResult } from "@/lib/types";

/** An invitation link (#token=…): accept as the signed-in account, sign in first, or create the account. */
export default function InvitePage() {
  const { t, locale } = useI18n();
  const p = t.publicPages.invite;
  useDocumentTitle(p.title);
  const router = useRouter();
  const client = useQueryClient();
  const token = useHashToken();
  const [invite, setInvite] = useState<InviteLookup | null>(null);
  const account = useAccountSecurity();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [twoFactor, setTwoFactor] = useState(false);
  const [terms, setTerms] = useState(false);

  useEffect(() => {
    if (token === undefined) return;
    if (token === null) {
      setInvite({ status: "invalid" });
      return;
    }
    api<InviteLookup>("invites/lookup", jsonRequest("POST", { token }))
      .then(setInvite)
      .catch(() => setInvite({ status: "invalid" }));
  }, [token]);

  const signedIn = account.data && !account.isError;
  const anonymous = account.error instanceof ApiError && account.error.status === 401;

  async function accept() {
    setBusy(true);
    setError("");
    try {
      const result = await api<{ workspace: { name: string } }>("invites/accept", jsonRequest("POST", { token }));
      toast.success(p.joined(result.workspace.name));
      await client.resetQueries();
      router.push("/");
    } catch (e) {
      setError(errorText(e, t));
      setBusy(false);
    }
  }

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api<LoginResult>("login", jsonRequest("POST", {
        email: invite?.email, password: new FormData(event.currentTarget).get("password"),
      }));
      if (result.two_factor_required) {
        setTwoFactor(true);
        setBusy(false);
        return;
      }
      await accept();
    } catch (e) {
      setError(errorText(e, t));
      setBusy(false);
    }
  }

  async function register(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("register", jsonRequest("POST", {
        email: invite?.email, password: new FormData(event.currentTarget).get("password"),
        accept_terms: terms, invite_token: token, locale,
      }));
      toast.success(p.joined(invite?.workspace ?? ""));
      await client.resetQueries();
      router.push("/");
    } catch (e) {
      setError(errorText(e, t));
      setBusy(false);
    }
  }

  async function signOut() {
    await api("logout", { method: "POST" }).catch(() => undefined);
    await client.resetQueries();
  }

  let body;
  if (!invite || (token && account.isPending)) {
    body = (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        {p.loading}
      </p>
    );
  } else if (invite.status !== "pending") {
    body = <FormNote tone="warning">{p.status[invite.status]}</FormNote>;
  } else if (twoFactor) {
    body = <TwoFactorStep onDone={() => accept()} onBack={() => setTwoFactor(false)} />;
  } else {
    const role = t.team.roles[invite.role ?? "viewer"];
    body = (
      <div className="space-y-4">
        <div className="space-y-1 text-sm">
          <p className="font-medium">{p.summary(invite.workspace ?? "", role)}</p>
          {invite.invited_by && <p className="text-muted-foreground">{p.invitedBy(invite.invited_by)}</p>}
          <p className="text-muted-foreground">{p.forEmail(invite.email ?? "")}</p>
        </div>
        {signedIn ? (
          account.data!.email.toLowerCase() === (invite.email ?? "").toLowerCase() ? (
            <>
              <p className="text-sm text-muted-foreground">{p.signedInAs(account.data!.email)}</p>
              <FormError message={error} />
              <Button className="w-full" disabled={busy} onClick={() => void accept()}>
                {busy && <Loader2 className="size-4 animate-spin" />}
                {p.accept}
              </Button>
            </>
          ) : (
            <>
              <FormNote tone="warning">{p.wrongAccount(invite.email ?? "")}</FormNote>
              <Button variant="outline" className="w-full" onClick={() => void signOut()}>{t.shell.signOut}</Button>
            </>
          )
        ) : anonymous && invite.account_exists ? (
          <form onSubmit={signIn} className="space-y-4">
            <div>
              <FieldLabel htmlFor="invite-email">{t.auth.email}</FieldLabel>
              <Input id="invite-email" value={invite.email ?? ""} readOnly className="bg-surface-2" />
            </div>
            <div>
              <FieldLabel htmlFor="invite-password">{t.auth.password}</FieldLabel>
              <Input id="invite-password" name="password" type="password" autoComplete="current-password" required
                     className="bg-surface-2" />
            </div>
            <FormError message={error} />
            <Button type="submit" className="w-full" disabled={busy}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              {p.signInToAccept}
            </Button>
            <Link href="/forgot-password" className="block text-center text-xs text-muted-foreground hover:text-foreground">
              {t.auth.forgot}
            </Link>
          </form>
        ) : (
          <form onSubmit={register} className="space-y-4">
            <div>
              <FieldLabel htmlFor="invite-email">{t.auth.email}</FieldLabel>
              <Input id="invite-email" value={invite.email ?? ""} readOnly className="bg-surface-2" />
            </div>
            <div>
              <FieldLabel htmlFor="invite-password">{t.auth.password}</FieldLabel>
              <Input id="invite-password" name="password" type="password" autoComplete="new-password" minLength={12}
                     required className="bg-surface-2" aria-describedby="invite-password-hint" />
              <p id="invite-password-hint" className="mt-1 text-xs text-muted-foreground">{t.account.password.hint}</p>
            </div>
            <TermsCheckbox checked={terms} onChange={setTerms} />
            <FormError message={error} />
            <Button type="submit" className="w-full" disabled={busy || !terms}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              {p.createToAccept}
            </Button>
          </form>
        )}
      </div>
    );
  }

  return (
    <PublicShell>
      <h1 className="mb-5 text-2xl font-semibold">{p.title}</h1>
      {body}
      <Link href="/" className="mt-5 block text-center text-sm text-muted-foreground hover:text-foreground">
        {p.open}
      </Link>
    </PublicShell>
  );
}
