"use client";

import { Sparkles, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FieldLabel, PageHeader, SoonBadge } from "@/components/reelforge/primitives";
import { AiSoonBanner, ChipField } from "@/components/reelforge/ai-tool";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

export default function MovieRecapPage() {
  const { t } = useI18n();
  const m = t.ai.recap;
  useDocumentTitle(m.title);
  return (
    <div className="space-y-6">
      <PageHeader title={m.title} subtitle={m.subtitle} />
      <AiSoonBanner />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
        <div className="panel space-y-5 p-5">
          <div>
            <FieldLabel htmlFor="recap-title">{m.movieTitle}</FieldLabel>
            <Input id="recap-title" placeholder={m.moviePlaceholder} className="bg-surface-2" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <FieldLabel htmlFor="recap-year">{m.year}</FieldLabel>
              <Input id="recap-year" inputMode="numeric" placeholder="2014" className="bg-surface-2" />
            </div>
            <div>
              <FieldLabel htmlFor="recap-imdb">{m.imdb}</FieldLabel>
              <Input id="recap-imdb" placeholder={m.imdbPlaceholder} className="bg-surface-2" />
            </div>
          </div>
          <div>
            <FieldLabel htmlFor="recap-ref">{m.reference}</FieldLabel>
            <Input id="recap-ref" placeholder={m.referencePlaceholder} className="bg-surface-2" />
          </div>
          <div>
            <FieldLabel htmlFor="recap-source">{m.source}</FieldLabel>
            <Textarea id="recap-source" rows={4} placeholder={m.sourcePlaceholder} className="resize-none bg-surface-2" />
            <div className="mt-2 flex flex-wrap gap-2">
              {m.uploads.map((label) => (
                <Button key={label} variant="outline" size="sm" disabled>
                  <Upload className="size-3.5" />
                  {label}
                </Button>
              ))}
            </div>
          </div>
          <ChipField label={m.contentType} options={m.contentTypes} initial={[2]} />
          <ChipField label={m.format} options={m.formats} />
          <ChipField label={m.style} options={m.styles} />
          <ChipField label={m.spoilers} options={m.spoilerOptions} initial={[2]} />
          <ChipField label={m.length} options={m.lengths} initial={[3]} />
          <Button className="w-full" disabled>
            <Sparkles className="size-4" /> {m.submit} <SoonBadge className="ml-1" />
          </Button>
        </div>
        <div className="panel stage-glow flex min-h-[420px] flex-col items-center justify-center p-10 text-center">
          <Sparkles className="size-6 text-primary" />
          <p className="mt-4 font-medium">{m.emptyTitle}</p>
          <p className="mt-1 max-w-sm text-sm text-muted-foreground">{m.emptyDescription}</p>
        </div>
      </div>
    </div>
  );
}
