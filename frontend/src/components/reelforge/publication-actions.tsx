"use client";

import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { keys } from "@/lib/queries";
import type { ChannelStatus, Publication } from "@/lib/types";
import { StatusBadge } from "./primitives";

/** A `datetime-local` value for a UTC ISO time, in the device's time zone. */
function localInput(iso: string | null | undefined) {
  if (!iso) return "";
  const date = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** Moves a scheduled or still-queued publication to another time, or cancels it, before its upload starts. */
export function ScheduleDialog({
  publication,
  mode,
  onClose,
}: {
  publication: Publication | null;
  mode: "reschedule" | "cancel";
  onClose: () => void;
}) {
  const { t } = useI18n();
  const client = useQueryClient();
  const showError = useErrorToast();
  const [busy, setBusy] = useState(false);
  const [value, setValue] = useState<string | null>(null);
  const current = value ?? localInput(publication?.scheduled_for);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!publication) return;
    setBusy(true);
    try {
      if (mode === "cancel") {
        await api(`publications/${encodeURIComponent(publication.id)}/cancel`, { method: "POST" });
        toast.success(t.publishing.cancelled);
      } else {
        const date = current ? new Date(current) : null;
        await api(
          `publications/${encodeURIComponent(publication.id)}/schedule`,
          jsonRequest("PUT", { scheduled_for: date && !Number.isNaN(date.getTime()) ? date.toISOString() : null }),
        );
        toast.success(t.publishing.rescheduled);
      }
      await client.invalidateQueries({ queryKey: keys.publications });
      setValue(null);
      onClose();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={Boolean(publication)}
      onOpenChange={(open) => {
        if (!open) {
          setValue(null);
          onClose();
        }
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{mode === "cancel" ? t.publishing.cancelTitle : t.publishing.rescheduleTitle}</DialogTitle>
          <DialogDescription>{mode === "cancel" ? t.publishing.cancelDescription : t.publishing.rescheduleHint}</DialogDescription>
        </DialogHeader>
        <form id="schedule-form" onSubmit={submit}>
          {mode === "reschedule" && (
            <Input
              type="datetime-local"
              aria-label={t.publishing.rescheduleTitle}
              value={current}
              onChange={(e) => setValue(e.target.value)}
              className="bg-surface"
            />
          )}
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            {t.common.cancel}
          </Button>
          <Button
            type="submit"
            form="schedule-form"
            variant={mode === "cancel" ? "destructive" : "default"}
            disabled={busy}
          >
            {busy && <Loader2 className="size-4 animate-spin" />}
            {mode === "cancel" ? t.publishing.cancel : t.publishing.reschedule}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** The channel card's badge: connected, not connected, server setup or authorization required. */
export function ChannelBadge({ status }: { status: ChannelStatus["status"] | undefined }) {
  const { t } = useI18n();
  const labels = {
    connected: t.status.channel.connected,
    not_connected: t.status.channel.notConnected,
    configuration_required: t.status.channel.configurationRequired,
    authorization_required: t.status.channel.authorizationRequired,
  };
  return (
    <StatusBadge
      status={status === "connected" ? "connected" : status === "authorization_required" ? "failed" : "not connected"}
      label={labels[status ?? "not_connected"]}
    />
  );
}
