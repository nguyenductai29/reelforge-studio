"use client";

import { useState, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
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
import { useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";

/** A confirmed admin action: ``run`` performs it, ``done`` refreshes what it changed. */
export type Confirm = { title: string; description: string; run: () => Promise<unknown>; done?: () => Promise<unknown> };

export function ConfirmDialog({ confirm, onClose }: { confirm: Confirm | null; onClose: () => void }) {
  const { t } = useI18n();
  const showError = useErrorToast();
  const [busy, setBusy] = useState(false);

  async function run() {
    if (!confirm) return;
    setBusy(true);
    try {
      await confirm.run();
      await confirm.done?.();
      toast.success(t.admin.saved);
      onClose();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AlertDialog open={Boolean(confirm)} onOpenChange={(open) => !open && !busy && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{confirm?.title}</AlertDialogTitle>
          <AlertDialogDescription>{confirm?.description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>{t.common.cancel}</AlertDialogCancel>
          <AlertDialogAction
            disabled={busy}
            onClick={(e) => {
              e.preventDefault();
              void run();
            }}
          >
            {busy && <Loader2 className="size-4 animate-spin" />}
            {t.common.confirm}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

// Radix Select items cannot use an empty string, so "no filter" gets its own token.
const ALL = "__all__";

/** A toolbar filter: one of ``options`` ([value, label]), or everything. */
export function FilterSelect({
  value,
  onChange,
  all,
  options,
}: {
  value: string;
  onChange: (value: string) => void;
  all: string;
  options: [string, string][];
}) {
  return (
    <Select value={value || ALL} onValueChange={(next) => onChange(next === ALL ? "" : next)}>
      <SelectTrigger className="h-8 w-auto min-w-[130px] bg-surface" aria-label={all}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>{all}</SelectItem>
        {options.map(([option, label]) => (
          <SelectItem key={option} value={option}>
            {label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 break-words text-sm">{children ?? "—"}</dd>
    </div>
  );
}
