"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { CheckCircle2, Circle, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { useI18n } from "@/lib/i18n";
import { useDashboard, useOnboarding } from "@/lib/queries";
import type { OnboardingStep } from "@/lib/types";
import { cn } from "@/lib/utils";

const LINKS: Record<OnboardingStep["key"], string> = {
  project: "/projects",
  channel: "/channels",
  template: "/workflows",
  generate: "/workflows",
  review: "/library",
  publish: "/publishing",
};

function storageKey(workspaceId: string) {
  return `reelforge.onboarding.hidden.${workspaceId}`;
}

/** Phase 26: the first steps of a studio, from its own data. For members, not system admins; no provider setup. */
export function OnboardingChecklist() {
  const { t } = useI18n();
  const o = t.onboarding;
  const { data } = useDashboard();
  const [hidden, setHidden] = useState(true);
  const workspaceId = data?.workspace.id;
  useEffect(() => {
    if (!workspaceId) return;
    try {
      setHidden(window.localStorage.getItem(storageKey(workspaceId)) === "1");
    } catch {
      setHidden(false);
    }
  }, [workspaceId]);
  const onboarding = useOnboarding(Boolean(data && !data.is_admin && !hidden));
  if (!data || data.is_admin || hidden || !onboarding.data || onboarding.data.complete) return null;
  const steps = onboarding.data.steps;
  const done = steps.filter((step) => step.done).length;

  function hide() {
    setHidden(true);
    try {
      if (workspaceId) window.localStorage.setItem(storageKey(workspaceId), "1");
    } catch {
      // A private window: hidden for this visit only.
    }
  }

  return (
    <section className="panel space-y-4 p-5" aria-labelledby="onboarding-title">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="onboarding-title" className="text-base font-semibold">{o.title}</h2>
          <p className="text-xs text-muted-foreground">{o.progress(done, steps.length)}</p>
        </div>
        <Button size="sm" variant="ghost" onClick={hide} aria-label={o.hide}>
          <X className="size-4" />
        </Button>
      </div>
      <Progress value={(done / steps.length) * 100} aria-label={o.progress(done, steps.length)} />
      <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {steps.map((step, index) => {
          const copy = o.steps[step.key];
          return (
            <li key={step.key} className={cn("flex gap-3 rounded-lg border border-border p-3", step.done && "border-dashed bg-transparent")}>
              {step.done
                ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" role="img" aria-label={o.done} />
                : <Circle className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />}
              <div className="min-w-0 space-y-1">
                <p className="text-sm font-medium">
                  {index + 1}. {copy.title}
                  {step.key === "channel" && <span className="ml-2 text-xs font-normal text-muted-foreground">{o.optional}</span>}
                </p>
                <p className="text-xs text-muted-foreground">{copy.hint}</p>
                {!step.done && (
                  <Link href={LINKS[step.key]} className="inline-block text-xs font-medium text-primary hover:underline">
                    {copy.action}
                  </Link>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
