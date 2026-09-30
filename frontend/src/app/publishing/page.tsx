"use client";

import Link from "next/link";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, RotateCcw, Send } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  PageHeader,
  PlatformIcon,
  SoonBadge,
  StatusBadge,
  platformLabel,
  type Platform,
} from "@/components/reelforge/primitives";
import { PublishDialog } from "@/components/reelforge/publish-dialog";
import { api } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { keys, usePublications, useYouTubeConnection } from "@/lib/queries";

const soonPlatforms: Platform[] = ["tiktok", "facebook", "instagram"];

export default function PublishingPage() {
  const { t, formatDateTime } = useI18n();
  useDocumentTitle(t.publishing.title);
  const client = useQueryClient();
  const showError = useErrorToast();
  const connection = useYouTubeConnection().data;
  const publications = usePublications().data ?? [];
  const [retrying, setRetrying] = useState<string | null>(null);

  async function retry(id: string) {
    setRetrying(id);
    try {
      await api(`youtube/publications/${encodeURIComponent(id)}/retry`, { method: "POST" });
      await client.invalidateQueries({ queryKey: keys.publications });
      toast.success(t.publishing.retried);
    } catch (error) {
      showError(error);
    } finally {
      setRetrying(null);
    }
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title={t.publishing.title}
        subtitle={t.publishing.subtitle}
        actions={
          <PublishDialog
            trigger={
              <Button>
                <Send className="size-4" /> {t.publishing.publishContent}
              </Button>
            }
          />
        }
      />

      <section>
        <h2 className="mb-4 text-base font-semibold">{t.publishing.connectedChannels}</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="panel space-y-3 p-4">
            <div className="flex items-center justify-between">
              <span className="flex size-9 items-center justify-center rounded-lg bg-surface-2">
                <PlatformIcon platform="youtube" />
              </span>
              <StatusBadge
                status={connection?.connected ? "connected" : "not connected"}
                label={connection?.connected ? t.status.channel.connected : t.status.channel.notConnected}
              />
            </div>
            <div>
              <p className="text-sm font-medium">{platformLabel.youtube}</p>
              <p className="text-xs text-muted-foreground">
                {connection?.connected ? t.channels.youtubeConnected : t.channels.notLinked}
              </p>
            </div>
            <Button asChild variant="outline" size="sm" className="w-full">
              <Link href="/channels">{connection?.connected ? t.common.manage : t.publishing.connect}</Link>
            </Button>
          </div>
          {soonPlatforms.map((platform) => (
            <div key={platform} className="panel space-y-3 p-4">
              <div className="flex items-center justify-between">
                <span className="flex size-9 items-center justify-center rounded-lg bg-surface-2">
                  <PlatformIcon platform={platform} />
                </span>
                <SoonBadge />
              </div>
              <div>
                <p className="text-sm font-medium">{platformLabel[platform]}</p>
                <p className="text-xs text-muted-foreground">{t.channels.soonHandle}</p>
              </div>
              <Button variant="outline" size="sm" className="w-full" disabled>
                {t.publishing.connect}
              </Button>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="mb-4 text-base font-semibold">{t.publishing.queue}</h2>
        <div className="panel divide-y divide-border">
          <div className="hidden grid-cols-[2fr_1fr_1fr_auto] gap-4 px-4 py-3 text-xs uppercase tracking-wider text-muted-foreground sm:grid">
            <span>{t.publishing.columns.content}</span>
            <span>{t.publishing.columns.channel}</span>
            <span>{t.publishing.columns.time}</span>
            <span>{t.publishing.columns.status}</span>
          </div>
          {publications.length === 0 && <p className="px-4 py-5 text-sm text-muted-foreground">{t.publishing.queueEmpty}</p>}
          {publications.map((publication) => (
            <div
              key={publication.id}
              className="grid gap-2 px-4 py-3 text-sm sm:grid-cols-[2fr_1fr_1fr_auto] sm:items-center sm:gap-4"
            >
              <div className="min-w-0">
                <p className="truncate font-medium">{publication.title}</p>
                {publication.last_error && (
                  <p className="mt-0.5 text-xs text-warning">
                    {t.publishing.errors[publication.last_error] ?? publication.last_error}
                  </p>
                )}
                <div className="mt-1 flex flex-wrap gap-3 text-xs">
                  {publication.remote_id && (
                    <a
                      href={`https://studio.youtube.com/video/${encodeURIComponent(publication.remote_id)}/edit`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-primary hover:underline"
                    >
                      {t.publishing.openStudio} <ExternalLink className="size-3" />
                    </a>
                  )}
                  {publication.can_retry && connection?.connected && (
                    <button
                      type="button"
                      disabled={retrying === publication.id}
                      onClick={() => void retry(publication.id)}
                      className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
                    >
                      <RotateCcw className="size-3" /> {t.publishing.retry}
                    </button>
                  )}
                </div>
              </div>
              <span className="flex items-center gap-2 text-muted-foreground">
                <PlatformIcon platform="youtube" />
                {platformLabel.youtube}
              </span>
              <span className="text-muted-foreground">{formatDateTime(publication.finished_at ?? publication.created_at)}</span>
              <StatusBadge
                status={publication.state}
                label={t.status.publication[publication.state]}
                className="justify-self-start"
              />
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
