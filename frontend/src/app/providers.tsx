"use client";

import { useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Clapperboard, Loader2, RefreshCw } from "lucide-react";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";
import { AppShell } from "@/components/reelforge/app-shell";
import { AuthScreen } from "@/components/reelforge/auth-screen";
import { ApiError } from "@/lib/api";
import { errorText } from "@/lib/errors";
import { I18nProvider, useI18n } from "@/lib/i18n";
import type { Locale } from "@/lib/i18n/config";
import { useDashboard } from "@/lib/queries";

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

function SessionGate({ children }: { children: ReactNode }) {
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
