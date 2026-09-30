"use client";

import { useState } from "react";
import { FileText, LibraryBig, Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState, PageHeader, SoonBadge } from "@/components/reelforge/primitives";
import { MediaThumb } from "@/components/reelforge/media-preview";
import Link from "next/link";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useDashboard, usePublications } from "@/lib/queries";
import { assetKind, formatBytes } from "@/lib/studio";

const tabs = ["all", "videos", "images", "scripts", "audio", "published"] as const;
type Tab = (typeof tabs)[number];

export default function LibraryPage() {
  const { t, formatRelative } = useI18n();
  useDocumentTitle(t.library.title);
  const { data } = useDashboard();
  const publications = usePublications().data ?? [];
  const [tab, setTab] = useState<Tab>("all");
  const [query, setQuery] = useState("");
  const assets = data?.assets ?? [];
  const published = new Set(publications.filter((p) => p.state === "succeeded").map((p) => p.asset_id));
  const q = query.trim().toLocaleLowerCase();

  const visible = assets.filter((asset) => {
    const kind = assetKind(asset.content_type);
    const inTab =
      tab === "all" ||
      (tab === "videos" && kind === "video") ||
      (tab === "images" && kind === "image") ||
      (tab === "audio" && kind === "audio") ||
      (tab === "published" && published.has(asset.id));
    return inTab && asset.filename.toLocaleLowerCase().includes(q);
  });

  const origin = (projectId: string | null, runId: string | null) => {
    const project = data?.projects.find((p) => p.id === projectId);
    if (runId) return project ? t.library.origin.workflow(project.title) : t.library.origin.workflowOnly;
    return project?.title ?? t.library.origin.upload;
  };

  return (
    <div className="space-y-6">
      <PageHeader title={t.library.title} subtitle={t.library.subtitle} />
      <div className="flex flex-wrap items-center gap-3">
        <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
          <TabsList className="h-auto flex-wrap">
            {tabs.map((key) => (
              <TabsTrigger key={key} value={key}>
                {t.library.tabs[key]}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t.library.searchPlaceholder}
            aria-label={t.library.searchPlaceholder}
            className="h-9 bg-surface pl-9"
          />
        </div>
      </div>

      {tab === "scripts" ? (
        <EmptyState icon={FileText} title={t.library.tabs.scripts} description={t.library.scriptsSoon} action={<SoonBadge />} />
      ) : assets.length === 0 ? (
        <EmptyState icon={LibraryBig} title={t.library.empty} description={t.library.emptyHint} />
      ) : visible.length === 0 ? (
        <div className="panel p-5 text-sm text-muted-foreground">{t.library.noMatch}</div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {visible.map((asset) => (
            <Link key={asset.id} href={`/media?asset=${asset.id}`} className="panel group overflow-hidden transition-colors hover:border-border-strong">
              <div className="aspect-video overflow-hidden bg-black">
                <MediaThumb asset={asset} />
              </div>
              <div className="space-y-1.5 p-4">
                <p className="truncate text-sm font-medium leading-snug group-hover:text-primary">{asset.filename}</p>
                <p className="text-xs text-muted-foreground">
                  {t.media.kinds[assetKind(asset.content_type)]} · {formatBytes(asset.bytes)}
                  {asset.created_at ? ` · ${formatRelative(asset.created_at)}` : ""}
                </p>
                <span className="inline-block max-w-full truncate rounded-md bg-surface-2 px-2 py-0.5 text-[11px] text-muted-foreground">
                  {published.has(asset.id) ? t.library.tabs.published : origin(asset.project_id, asset.run_id)}
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
