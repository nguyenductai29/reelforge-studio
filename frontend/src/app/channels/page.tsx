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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { PageHeader, PlatformIcon, platformLabel } from "@/components/reelforge/primitives";
import { ChannelBadge } from "@/components/reelforge/publication-actions";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useDocumentTitle, useSearchParam } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import type { Dictionary } from "@/lib/i18n/vi";
import { keys, useChannels } from "@/lib/queries";
import type { ChannelId, ChannelStatus } from "@/lib/types";

type Cap = keyof Dictionary["channels"]["caps"];

const CAPS: Record<ChannelId, Cap[]> = {
  youtube: ["publishVideo", "publishShort", "privateUpload", "schedule"],
  tiktok: ["publishVideo", "schedule"],
  facebook: ["publishReel", "schedule"],
};
const CHANNELS: ChannelId[] = ["youtube", "tiktok", "facebook"];

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

/** One channel: status, account, and the connect, choose-Page or disconnect actions (owner only on the server). */
function ChannelCard({ status, onDisconnect }: { status: ChannelStatus; onDisconnect: (channel: ChannelId) => void }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const showError = useErrorToast();
  const [busy, setBusy] = useState(false);
  const channel = status.channel;
  const name = platformLabel[channel];
  const connected = status.status === "connected";
  const note =
    status.status === "configuration_required"
      ? t.channels.configRequired
      : status.reason
        ? (t.channels.reasons[status.reason] ?? status.reason)
        : channel === "tiktok"
          ? t.channels.tiktokNote
          : channel === "facebook"
            ? t.channels.facebookNote
            : connected
              ? t.channels.youtubeConnected
              : t.channels.ownerOnly;

  async function connect() {
    setBusy(true);
    try {
      const start = await api<{ url: string }>(`channels/${channel}/authorization`);
      window.location.assign(start.url);
    } catch (error) {
      showError(error);
      setBusy(false);
    }
  }

  async function choosePage(pageId: string) {
    setBusy(true);
    try {
      await api("channels/facebook/page", jsonRequest("PUT", { page_id: pageId }));
      await client.invalidateQueries({ queryKey: keys.channels });
      toast.success(t.channels.pageSaved);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel space-y-4 p-5">
      <div className="flex items-start gap-3">
        <span className="flex size-11 items-center justify-center rounded-xl bg-surface-2">
          <PlatformIcon platform={channel} className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-medium">{name}</p>
          <p className="truncate text-xs text-muted-foreground">
            {status.account_name
              ? channel === "facebook"
                ? t.channels.pageAs(status.account_name)
                : t.channels.accountAs(status.account_name)
              : connected
                ? t.status.channel.connected
                : t.channels.notLinked}
          </p>
        </div>
        <ChannelBadge status={status.status} />
      </div>
      <p className="text-xs text-muted-foreground">{note}</p>
      <Caps caps={CAPS[channel]} />
      {channel === "facebook" && status.reason === "page_required" && (status.pages?.length ?? 0) > 0 && (
        <Select onValueChange={(value) => void choosePage(value)} disabled={busy}>
          <SelectTrigger className="bg-surface" aria-label={t.channels.choosePage}>
            <SelectValue placeholder={t.channels.choosePage} />
          </SelectTrigger>
          <SelectContent>
            {status.pages!.map((page) => (
              <SelectItem key={page.id} value={page.id}>
                {page.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      <div className="flex gap-2">
        {status.status !== "configuration_required" && (connected || status.status === "authorization_required") && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => onDisconnect(channel)}>
            {t.channels.disconnect}
          </Button>
        )}
        {status.status !== "configuration_required" && !connected && status.reason !== "page_required" && (
          <Button size="sm" disabled={busy} onClick={() => void connect()}>
            {busy && <Loader2 className="size-3.5 animate-spin" />}
            {busy ? t.channels.connectingTo(name) : t.channels.connect}
          </Button>
        )}
      </div>
    </div>
  );
}

export default function ChannelsPage() {
  const { t } = useI18n();
  useDocumentTitle(t.channels.title);
  const client = useQueryClient();
  const showError = useErrorToast();
  const channels = useChannels();
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<ChannelId | null>(null);
  const connectedParam = useSearchParam("connected");

  useEffect(() => {
    if (connectedParam && (CHANNELS as string[]).includes(connectedParam)) {
      toast.success(t.channels.connectedPlatform(platformLabel[connectedParam as ChannelId]));
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, [connectedParam, t]);

  async function disconnect() {
    if (!confirm) return;
    setBusy(true);
    try {
      await api(`channels/${confirm}`, { method: "DELETE" });
      await client.invalidateQueries({ queryKey: keys.channels });
      await client.invalidateQueries({ queryKey: keys.youtube });
      toast.success(t.channels.disconnectedPlatform(platformLabel[confirm]));
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader title={t.channels.title} subtitle={t.channels.subtitle} />
      <div className="grid gap-4 sm:grid-cols-2">
        {channels.isPending && <Loader2 className="size-5 animate-spin text-muted-foreground" />}
        {CHANNELS.map((channel) => {
          const status = channels.data?.find((item) => item.channel === channel);
          return status ? <ChannelCard key={channel} status={status} onDisconnect={setConfirm} /> : null;
        })}
      </div>

      <AlertDialog open={Boolean(confirm)} onOpenChange={(open) => !open && setConfirm(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirm ? t.channels.disconnectPlatform(platformLabel[confirm]) : ""}</AlertDialogTitle>
            <AlertDialogDescription>{t.channels.disconnectPlatformDescription}</AlertDialogDescription>
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
