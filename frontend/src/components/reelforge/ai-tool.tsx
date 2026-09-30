"use client";

import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import { ComingSoonBanner, FieldLabel, OptionChips } from "./primitives";

export function AiSoonBanner() {
  const { t } = useI18n();
  return (
    <ComingSoonBanner
      title={t.ai.soonTitle}
      description={t.ai.soonDescription}
      action={
        <Button asChild size="sm" variant="outline">
          <Link href="/workflows">{t.ai.openWorkflows}</Link>
        </Button>
      }
    />
  );
}

/** Option chips that keep their own selection; the tools they configure are not live yet. */
export function ChipField({
  label,
  options,
  initial = [0],
  multi = false,
}: {
  label: string;
  options: string[];
  initial?: number[];
  multi?: boolean;
}) {
  const [value, setValue] = useState(() => initial.map((i) => String(i)));
  return (
    <div>
      <FieldLabel>{label}</FieldLabel>
      <OptionChips
        multi={multi}
        options={options.map((option, index) => ({ value: String(index), label: option }))}
        value={value}
        onChange={setValue}
      />
    </div>
  );
}
