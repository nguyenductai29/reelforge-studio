"use client";

import { useState } from "react";
import { Image as ImageIcon, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { FieldLabel, OptionChips, PageHeader, SoonBadge } from "@/components/reelforge/primitives";
import { AiSoonBanner, ChipField } from "@/components/reelforge/ai-tool";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const ratios = ["16:9", "9:16", "1:1", "2:3"];

export default function ImageGeneratorPage() {
  const { t } = useI18n();
  const g = t.ai.image;
  useDocumentTitle(g.title);
  const [ratio, setRatio] = useState(["16:9"]);
  const aspect = { "16:9": "aspect-video", "9:16": "aspect-[9/16]", "1:1": "aspect-square", "2:3": "aspect-[2/3]" }[ratio[0]!];

  return (
    <div className="space-y-6">
      <PageHeader title={g.title} subtitle={g.subtitle} />
      <AiSoonBanner />
      <div className="panel space-y-4 p-5">
        <Textarea rows={3} placeholder={g.promptPlaceholder} aria-label={g.title} className="resize-none bg-surface-2" />
        <ChipField label={g.preset} options={g.presets} />
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <FieldLabel>{g.aspect}</FieldLabel>
            <OptionChips options={ratios} value={ratio} onChange={setRatio} />
          </div>
          <ChipField label={g.style} options={g.styles} />
        </div>
        <Button disabled>
          <Sparkles className="size-4" /> {g.submit} <SoonBadge className="ml-1" />
        </Button>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className="panel overflow-hidden">
            <div className={cn("flex items-center justify-center bg-surface-2", aspect)}>
              <div className="flex flex-col items-center gap-2 text-xs text-muted-foreground">
                <ImageIcon className="size-5" />
                {g.placeholder}
              </div>
            </div>
            <div className="flex flex-wrap gap-1.5 p-3">
              {g.actions.map((action) => (
                <button
                  key={action}
                  type="button"
                  disabled
                  className="rounded-md border border-border bg-surface-2 px-2 py-1 text-[11px] text-muted-foreground disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {action}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
