"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PlatformIcon, platformLabel } from "@/components/reelforge/primitives";
import { api, jsonRequest } from "@/lib/api";
import { errorText } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { keys } from "@/lib/queries";

const CHANNELS = ["tiktok", "facebook"] as const;
type Channel = (typeof CHANNELS)[number];

/**
 * Where TikTok and Facebook send the browser back after consent (``TIKTOK_REDIRECT_URI`` and
 * ``FACEBOOK_REDIRECT_URI`` point here). The one-time code is posted to the API, which checks the
 * single-use state against the signed-in owner; no token ever reaches the browser.
 */
export default function ChannelCallback() {
  const { channel: raw } = useParams<{ channel: string }>();
  const channel = (CHANNELS as readonly string[]).includes(raw) ? (raw as Channel) : null;
  const { t } = useI18n();
  const name = channel ? platformLabel[channel] : "";
  useDocumentTitle(name || t.channels.title);
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
    if (!channel || query.get("error") || !state || !code) {
      setMessage(t.channels.callbackFailed);
      return;
    }
    api(`channels/${channel}/callback`, jsonRequest("POST", { state, code }))
      .then(async () => {
        await client.invalidateQueries({ queryKey: keys.channels });
        router.replace(`/channels?connected=${channel}`);
      })
      .catch((error) => setMessage(errorText(error, t) || t.channels.callbackFailed));
  }, [channel, client, router, t]);

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="panel max-w-md space-y-4 p-6 text-center">
        {channel && (
          <span className="mx-auto flex size-11 items-center justify-center rounded-xl bg-surface-2">
            <PlatformIcon platform={channel} className="size-5" />
          </span>
        )}
        <h1 className="text-xl font-semibold">{name || t.channels.title}</h1>
        {message ? (
          <p className="text-sm text-muted-foreground">{message}</p>
        ) : (
          <p className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> {t.channels.callbackWorking}
          </p>
        )}
        <Button asChild variant="outline" size="sm">
          <Link href="/channels">{t.youtube.back}</Link>
        </Button>
      </div>
    </div>
  );
}
