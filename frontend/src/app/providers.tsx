"use client";

import { usePathname } from "next/navigation";
import { useState, type FormEvent, type ReactNode } from "react";
import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { Clapperboard, Loader2, RefreshCw } from "lucide-react";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AppShell } from "@/components/reelforge/app-shell";
import { AuthScreen } from "@/components/reelforge/auth-screen";
import { FieldLabel } from "@/components/reelforge/primitives";
import { FormError, PublicShell } from "@/components/reelforge/public-shell";
import { api, ApiError, jsonRequest } from "@/lib/api";
import { errorText } from "@/lib/errors";
import { I18nProvider, useI18n } from "@/lib/i18n";
import type { Locale } from "@/lib/i18n/config";
import { useAuthStatus, useDashboard } from "@/lib/queries";

// Reachable signed out (Phase 22/26): emailed links, the legal pages. They render without the studio shell.
const PUBLIC_PATHS = ["/forgot-password", "/reset-password", "/verify-email", "/invite", "/terms", "/privacy"];

function makeClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        // Client errors (auth, missing records) will not change on retry.
        retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      },
    },
  });
}

/** Signed in, but a member of no studio (removed, or left the last one): create one, or sign out. */
function NoWorkspace() {
  const { t } = useI18n();
  const n = t.team.noWorkspace;
  const client = useQueryClient();
  const status = useAuthStatus();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("workspaces", jsonRequest("POST", { name: new FormData(event.currentTarget).get("name") }));
      await client.resetQueries();
    } catch (e) {
      setError(errorText(e, t));
      setBusy(false);
    }
  }

  async function signOut() {
    await api("logout", { method: "POST" }).catch(() => undefined);
    await client.resetQueries();
  }

  return (
    <PublicShell>
      <h1 className="text-xl font-semibold">{n.title}</h1>
      <p className="mt-2 text-sm text-muted-foreground">{n.hint}</p>
      {status.data?.registration_enabled && (
        <form onSubmit={create} className="mt-5 space-y-3">
          <div>
            <FieldLabel htmlFor="studio-name">{t.team.switcher.createName}</FieldLabel>
            <Input id="studio-name" name="name" required maxLength={100} className="bg-surface-2" />
          </div>
          <FormError message={error} />
          <Button type="submit" className="w-full" disabled={busy}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {n.create}
          </Button>
        </form>
      )}
      <Button variant="outline" className="mt-3 w-full" onClick={() => void signOut()}>{n.signOut}</Button>
    </PublicShell>
  );
}

function SessionGate({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  if (PUBLIC_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`))) return <>{children}</>;
  return <PrivateGate>{children}</PrivateGate>;
}

function PrivateGate({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const dashboard = useDashboard();

  if (dashboard.isPending) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background">
        <span className="brand-fill flex size-10 items-center justify-center rounded-xl">
          <Clapperboard className="size-5" />
        </span>
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          {t.auth.loadingStudio}
        </p>
      </div>
    );
  }
  if (dashboard.error instanceof ApiError && dashboard.error.status === 401) return <AuthScreen />;
  if (dashboard.error instanceof ApiError && dashboard.error.status === 403 && dashboard.error.detail === "No workspace") {
    return <NoWorkspace />;
  }
  if (dashboard.error) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4">
        <div className="panel max-w-md p-6 text-center">
          <p className="font-medium">{t.auth.loadFailed}</p>
          <p className="mt-2 text-sm text-muted-foreground">{errorText(dashboard.error, t)}</p>
          <Button className="mt-5" variant="outline" onClick={() => void dashboard.refetch()}>
            <RefreshCw className="size-4" />
            {t.common.retry}
          </Button>
        </div>
      </div>
    );
  }
  return <AppShell>{children}</AppShell>;
}

export function Providers({ locale, children }: { locale: Locale; children: ReactNode }) {
  const [client] = useState(makeClient);
  return (
    <I18nProvider initialLocale={locale}>
      <QueryClientProvider client={client}>
        <TooltipProvider delayDuration={200}>
          <SessionGate>{children}</SessionGate>
          <Toaster position="bottom-right" theme="dark" />
        </TooltipProvider>
      </QueryClientProvider>
    </I18nProvider>
  );
}
