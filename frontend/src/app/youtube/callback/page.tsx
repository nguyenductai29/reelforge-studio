"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PlatformIcon } from "@/components/reelforge/primitives";
import { api, jsonRequest } from "@/lib/api";
import { errorText } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { keys } from "@/lib/queries";

export default function YouTubeCallback() {
  const { t } = useI18n();
  useDocumentTitle(t.youtube.title);
  const router = useRouter();
  const client = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const query = new URLSearchParams(window.location.search);
    const state = query.get("state");
    const code = query.get("code");
    // The one-time code must not stay in the address bar or history.
    window.history.replaceState(null, "", window.location.pathname);
    if (query.get("error")) {
      setMessage(t.youtube.cancelled);
      return;
    }
    if (!state || !code) {
      setMessage(t.youtube.missingCode);
      return;
    }
    api("youtube/callback", jsonRequest("POST", { state, code }))
      .then(async () => {
        await client.invalidateQueries({ queryKey: keys.youtube });
        router.replace("/channels?connected=youtube");
      })
      .catch((error) => setMessage(errorText(error, t) || t.youtube.failed));
  }, [client, router, t]);

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="panel max-w-md space-y-4 p-6 text-center">
        <span className="mx-auto flex size-11 items-center justify-center rounded-xl bg-surface-2">
          <PlatformIcon platform="youtube" className="size-5" />
        </span>
        <h1 className="text-xl font-semibold">{t.youtube.title}</h1>
        {message ? (
          <p className="text-sm text-muted-foreground">{message}</p>
        ) : (
          <p className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> {t.youtube.finishing}
          </p>
        )}
        <Button asChild variant="outline" size="sm">
          <Link href="/channels">{t.youtube.back}</Link>
        </Button>
      </div>
    </div>
  );
}
