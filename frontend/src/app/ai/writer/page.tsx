"use client";

import { Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FieldLabel, PageHeader, SoonBadge } from "@/components/reelforge/primitives";
import { AiSoonBanner, ChipField } from "@/components/reelforge/ai-tool";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

export default function WriterPage() {
  const { t } = useI18n();
  const w = t.ai.writer;
  useDocumentTitle(w.title);
  return (
    <div className="space-y-6">
      <PageHeader title={w.title} subtitle={w.subtitle} />
      <AiSoonBanner />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)]">
        <div className="panel space-y-5 p-5">
          <div>
            <FieldLabel htmlFor="writer-topic">{w.topic}</FieldLabel>
            <Textarea id="writer-topic" rows={4} placeholder={w.topicPlaceholder} className="resize-none bg-surface-2" />
          </div>
          <ChipField label={w.output} options={w.outputs} />
          <ChipField label={w.platform} options={w.platforms} initial={[1]} />
          <ChipField label={w.tone} options={w.tones} initial={[2]} />
          <div>
            <FieldLabel htmlFor="writer-keywords">{w.keywords}</FieldLabel>
            <Input id="writer-keywords" placeholder={w.keywordsPlaceholder} className="bg-surface-2" />
          </div>
          <Button className="w-full" disabled>
            <Sparkles className="size-4" /> {w.submit} <SoonBadge className="ml-1" />
          </Button>
        </div>
        <div className="panel stage-glow flex min-h-[360px] items-center justify-center p-10 text-center text-sm text-muted-foreground">
          {w.placeholder}
        </div>
      </div>
    </div>
  );
}
