"use client";

import { useState } from "react";
import { FileText, LibraryBig, Search } from "lucide-react";
import { QueryError } from "@/components/reelforge/query-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState, PageHeader } from "@/components/reelforge/primitives";
import { MediaThumb } from "@/components/reelforge/media-preview";
import Link from "next/link";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useDashboard, usePublications, useScripts } from "@/lib/queries";
import { assetKind, formatBytes } from "@/lib/studio";

const tabs = ["all", "videos", "images", "scripts", "audio", "published"] as const;
const SCRIPT_PAGE = 12;
type Tab = (typeof tabs)[number];

export default function LibraryPage() {
  const { t, formatRelative } = useI18n();
  useDocumentTitle(t.library.title);
  const { data } = useDashboard();
  const publications = usePublications().data ?? [];
  const [tab, setTab] = useState<Tab>("all");
  const [query, setQuery] = useState("");
  const [scriptOffset, setScriptOffset] = useState(0);
  const scripts = useScripts(scriptOffset, undefined, SCRIPT_PAGE);
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
    <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)} className="space-y-6">
      <PageHeader title={t.library.title} subtitle={t.library.subtitle} />
      <div className="flex flex-wrap items-center gap-3">
        <TabsList className="h-auto flex-wrap">
          {tabs.map((key) => (
            <TabsTrigger key={key} value={key}>
              {t.library.tabs[key]}
            </TabsTrigger>
          ))}
        </TabsList>
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

      {/* One panel for the selected tab: the tabs filter what it shows. */}
      <TabsContent value={tab} className="mt-0">
        {tab === "scripts" ? (
          scripts.isError ? (
            <QueryError error={scripts.error} onRetry={() => void scripts.refetch()} />
          ) : !scripts.data?.items.length ? (
            <EmptyState icon={FileText} title={t.library.tabs.scripts} description={t.library.noScripts} />
          ) : (
            <div className="space-y-3">
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                {scripts.data.items.map((script) => {
                  const project = data?.projects.find((p) => p.id === script.project_id);
                  return (
                    <Link
                      key={script.step_id}
                      href={`/workflows/${script.workflow_id}?run=${script.run_id}`}
                      className="panel group flex flex-col gap-2 p-4 transition-colors hover:border-border-strong"
                    >
                      <p className="text-[11px] text-muted-foreground">
                        {t.nodes[script.node_type]?.name ?? script.node_type} · {t.workspace.words(script.words)} ·{" "}
                        {formatRelative(script.created_at)}
                      </p>
                      <p className="line-clamp-6 whitespace-pre-line text-sm leading-relaxed">{script.text}</p>
                      <span className="mt-auto inline-block max-w-full truncate rounded-md bg-surface-2 px-2 py-0.5 text-[11px] text-muted-foreground group-hover:text-primary">
                        {project?.title ?? t.library.origin.workflowOnly}
                      </span>
                    </Link>
                  );
                })}
              </div>
              <div className="flex items-center justify-end gap-2">
                <Button variant="outline" size="sm" disabled={scriptOffset === 0}
                        onClick={() => setScriptOffset(Math.max(0, scriptOffset - SCRIPT_PAGE))}>
                  {t.table.previous}
                </Button>
                <Button variant="outline" size="sm" disabled={scriptOffset + SCRIPT_PAGE >= scripts.data.total}
                        onClick={() => setScriptOffset(scriptOffset + SCRIPT_PAGE)}>
                  {t.table.next}
                </Button>
              </div>
            </div>
          )
        ) : assets.length === 0 ? (
          <EmptyState icon={LibraryBig} title={t.library.empty} description={t.library.emptyHint} />
        ) : visible.length === 0 ? (
          <div className="panel p-5 text-sm text-muted-foreground">{t.library.noMatch}</div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 3xl:grid-cols-5">
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
      </TabsContent>
    </Tabs>
  );
}
