"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, RefreshCw, WifiOff } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { errorText } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** A section whose data did not load: what happened, in words (never a raw response), and a way to try again. */
export function QueryError({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  const { t } = useI18n();
  return (
    <div role="alert" className={cn("panel flex flex-wrap items-center gap-3 p-4 text-sm", className)}>
      <AlertTriangle className="size-4 shrink-0 text-destructive" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="font-medium">{t.common.loadFailed}</p>
        <p className="text-muted-foreground">{errorText(error, t)}</p>
      </div>
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry}>
          <RefreshCw className="size-3.5" aria-hidden />
          {t.common.retry}
        </Button>
      )}
    </div>
  );
}

/** While the browser is offline: a notice above every page (requests wait; nothing is lost in forms). */
export function OfflineBanner({ className }: { className?: string }) {
  const { t } = useI18n();
  const [online, setOnline] = useState(true);
  useEffect(() => {
    setOnline(navigator.onLine);
    const up = () => {
      setOnline(true);
      toast.success(t.common.backOnline);
    };
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, [t]);
  if (online) return null;
  return (
    <div role="status" className={cn("mb-4 flex items-center gap-2 rounded-lg border border-warning/40 bg-warning/10 px-4 py-3 text-sm",
                                     className)}>
      <WifiOff className="size-4 shrink-0" aria-hidden />
      {t.common.offline}
    </div>
  );
}
