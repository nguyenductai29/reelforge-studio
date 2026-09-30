"use client";

import { Recycle, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader, SoonBadge } from "@/components/reelforge/primitives";
import { AiSoonBanner, ChipField } from "@/components/reelforge/ai-tool";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

export default function RepurposePage() {
  const { t } = useI18n();
  const r = t.ai.repurpose;
  useDocumentTitle(r.title);
  return (
    <div className="space-y-6">
      <PageHeader title={r.title} subtitle={r.subtitle} />
      <AiSoonBanner />
      <div className="panel p-5">
        <Tabs defaultValue="text">
          <TabsList className="h-auto flex-wrap">
            <TabsTrigger value="text">{r.tabs.text}</TabsTrigger>
            <TabsTrigger value="url">{r.tabs.url}</TabsTrigger>
            <TabsTrigger value="upload">{r.tabs.upload}</TabsTrigger>
          </TabsList>
          <TabsContent value="text" className="mt-4">
            <Textarea rows={6} placeholder={r.textPlaceholder} aria-label={r.tabs.text} className="resize-none bg-surface-2" />
          </TabsContent>
          <TabsContent value="url" className="mt-4">
            <Input placeholder={r.urlPlaceholder} aria-label={r.tabs.url} className="bg-surface-2" />
          </TabsContent>
          <TabsContent value="upload" className="mt-4">
            <div className="flex flex-col items-center rounded-xl border border-dashed border-border-strong p-10 text-center">
              <Upload className="size-5 text-muted-foreground" />
              <p className="mt-3 text-sm">{r.dropTitle}</p>
              <p className="text-xs text-muted-foreground">MP4, MOV, MP3, WAV, SRT, TXT</p>
            </div>
          </TabsContent>
        </Tabs>
        <div className="mt-5">
          <ChipField label={r.into} options={r.outputs} initial={[1, 2, 4]} multi />
        </div>
        <Button className="mt-5" disabled>
          <Recycle className="size-4" /> {r.submit} <SoonBadge className="ml-1" />
        </Button>
      </div>
    </div>
  );
}
