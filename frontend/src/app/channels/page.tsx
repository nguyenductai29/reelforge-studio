"use client";

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  PageHeader,
  PlatformIcon,
  SoonBadge,
  StatusBadge,
  platformLabel,
  type Platform,
} from "@/components/reelforge/primitives";
import { api } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useDocumentTitle, useSearchParam } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import type { Dictionary } from "@/lib/i18n/vi";
import { keys, useYouTubeConnection } from "@/lib/queries";

type Cap = keyof Dictionary["channels"]["caps"];

const soon: { platform: Platform; caps: Cap[] }[] = [
  { platform: "tiktok", caps: ["publishVideo", "schedule"] },
  { platform: "facebook", caps: ["publishVideo", "publishReel", "schedule", "analytics"] },
  { platform: "instagram", caps: ["publishReel", "schedule"] },
];

function Caps({ caps }: { caps: Cap[] }) {
  const { t } = useI18n();
  return (
    <div className="flex flex-wrap gap-1.5">
      {caps.map((cap) => (
        <span key={cap} className="rounded-md bg-surface-2 px-2 py-1 text-[11px] text-muted-foreground">
          {t.channels.caps[cap]}
        </span>
      ))}
    </div>
  );
}

export default function ChannelsPage() {
  const { t, formatDateTime } = useI18n();
  useDocumentTitle(t.channels.title);
  const client = useQueryClient();
  const showError = useErrorToast();
  const connection = useYouTubeConnection();
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const connectedParam = useSearchParam("connected");

  useEffect(() => {
    if (connectedParam === "youtube") {
      toast.success(t.channels.connected);
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, [connectedParam, t]);

  async function connect() {
    setBusy(true);
    try {
      const start = await api<{ url: string }>("youtube/authorization");
      window.location.assign(start.url);
    } catch (error) {
      showError(error);
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      await api("youtube/connection", { method: "DELETE" });
      await client.invalidateQueries({ queryKey: keys.youtube });
      toast.success(t.channels.disconnected);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
      setConfirm(false);
    }
  }

  const yt = connection.data;

  return (
    <div className="space-y-6">
      <PageHeader title={t.channels.title} subtitle={t.channels.subtitle} />
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="panel space-y-4 p-5">
          <div className="flex items-start gap-3">
            <span className="flex size-11 items-center justify-center rounded-xl bg-surface-2">
              <PlatformIcon platform="youtube" className="size-5" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="font-medium">{platformLabel.youtube}</p>
              <p className="text-xs text-muted-foreground">{yt?.connected ? t.channels.youtubeConnected : t.channels.notLinked}</p>
            </div>
            {connection.isPending ? (
              <Loader2 className="size-4 animate-spin text-muted-foreground" />
            ) : (
              <StatusBadge
                status={yt?.connected ? "connected" : "not connected"}
                label={yt?.connected ? t.status.channel.connected : t.status.channel.notConnected}
              />
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            {platformLabel.youtube} ·{" "}
            {yt?.connected && yt.expires_at ? t.channels.tokenExpires(formatDateTime(yt.expires_at)) : t.channels.ownerOnly}
          </p>
          <Caps caps={["publishVideo", "publishShort", "privateUpload"]} />
          <div className="flex gap-2">
            {yt?.connected ? (
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => setConfirm(true)}>
                {t.channels.disconnect}
              </Button>
            ) : (
              <Button size="sm" disabled={busy || !yt} onClick={() => void connect()}>
                {busy && <Loader2 className="size-3.5 animate-spin" />}
                {busy ? t.channels.connecting : t.channels.connect}
              </Button>
            )}
          </div>
        </div>

        {soon.map((channel) => (
          <div key={channel.platform} className="panel space-y-4 p-5">
            <div className="flex items-start gap-3">
              <span className="flex size-11 items-center justify-center rounded-xl bg-surface-2">
                <PlatformIcon platform={channel.platform} className="size-5" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="font-medium">{platformLabel[channel.platform]}</p>
                <p className="text-xs text-muted-foreground">{t.channels.soonHandle}</p>
              </div>
              <SoonBadge />
            </div>
            <p className="text-xs text-muted-foreground">
              {platformLabel[channel.platform]} · {t.channels.soonSync}
            </p>
            <Caps caps={channel.caps} />
            <div className="flex gap-2">
              <Button size="sm" disabled>
                {t.channels.connect}
              </Button>
            </div>
          </div>
        ))}
      </div>

      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t.channels.disconnectTitle}</AlertDialogTitle>
            <AlertDialogDescription>{t.channels.disconnectDescription}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction
              disabled={busy}
              onClick={(e) => {
                e.preventDefault();
                void disconnect();
              }}
            >
              {t.channels.disconnect}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
