"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  BadgeDollarSign,
  Bell,
  CheckCheck,
  Coins,
  HardDrive,
  LifeBuoy,
  Share2,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { api, jsonRequest } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import type { Dictionary } from "@/lib/i18n/vi";
import { keys, useNotifications, useUnreadCount } from "@/lib/queries";
import type { AppNotification } from "@/lib/types";
import { cn } from "@/lib/utils";

const ICONS: Record<string, LucideIcon> = {
  run: Workflow,
  publish: Share2,
  payment: BadgeDollarSign,
  credits: Coins,
  storage: HardDrive,
  support: LifeBuoy,
};
const LiveContext = createContext(false);

/** Whether the live stream is connected; when it is not, lists and the badge poll instead. */
export function useNotificationsLive() {
  return useContext(LiveContext);
}

/** The localized title and message of a notification; the server's English text when the type is unknown. */
export function notificationText(t: Dictionary, item: AppNotification): { title: string; message: string } {
  const kind = (t.notifications.kinds as Record<string, { title: (p: AppNotification["params"]) => string;
    message: (p: AppNotification["params"]) => string } | undefined>)[item.type];
  if (!kind) return { title: item.title, message: item.message };
  return { title: kind.title(item.params), message: kind.message(item.params) };
}

export function notificationIcon(type: string): LucideIcon {
  return ICONS[type.split(".")[0] ?? ""] ?? Bell;
}

/**
 * One Server-Sent Events connection per tab (GET /api/notifications/stream, authenticated by the session cookie).
 * The browser reconnects by itself and resumes from the last event id; after a hard failure (for example a
 * restarting API) it is reopened with a growing delay, and meanwhile the badge and lists poll every 30 s.
 */
export function NotificationStream({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const [live, setLive] = useState(false);
  // Read at event time, so switching language does not reopen the connection.
  const text = useRef(t);
  text.current = t;

  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    let stopped = false;

    const open = () => {
      source = new EventSource("/api/notifications/stream");
      source.addEventListener("unread", (event) => {
        failures = 0;
        setLive(true);
        client.setQueryData(keys.unread, JSON.parse((event as MessageEvent).data).unread);
      });
      source.addEventListener("notification", (event) => {
        const item = JSON.parse((event as MessageEvent).data) as AppNotification;
        void client.invalidateQueries({ queryKey: keys.notifications });
        if (item.type.startsWith("support.")) void client.invalidateQueries({ queryKey: keys.support });
        if (document.visibilityState === "visible") {
          const shown = notificationText(text.current, item);
          toast(shown.title, { description: shown.message });
        }
      });
      source.addEventListener("end", () => {
        // Signed out (or the account was locked): stop until the page reloads.
        stopped = true;
        source?.close();
        setLive(false);
      });
      source.onerror = () => {
        setLive(false);
        if (source?.readyState === EventSource.CLOSED && !stopped) {
          failures += 1;
          timer = setTimeout(open, Math.min(60000, 5000 * 2 ** Math.min(failures, 4)));
        }
      };
    };
    open();
    return () => {
      stopped = true;
      clearTimeout(timer);
      source?.close();
    };
  }, [client]);

  return <LiveContext.Provider value={live}>{children}</LiveContext.Provider>;
}

export function useMarkRead() {
  const client = useQueryClient();
  const showError = useErrorToast();
  return async (id?: number) => {
    try {
      const result = await api<{ unread: number }>(
        id === undefined ? "notifications/read-all" : `notifications/${id}/read`,
        jsonRequest("POST", {}),
      );
      client.setQueryData(keys.unread, result.unread);
      await client.invalidateQueries({ queryKey: keys.notifications });
    } catch (error) {
      showError(error);
    }
  };
}

export function NotificationRow({ item, onOpen, compact = false }: { item: AppNotification; onOpen: () => void;
  compact?: boolean }) {
  const { t, formatRelative } = useI18n();
  const text = notificationText(t, item);
  const Icon = notificationIcon(item.type);
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "flex w-full items-start gap-3 rounded-lg p-2.5 text-left transition-colors hover:bg-surface-2",
        !item.read_at && "bg-[color-mix(in_oklab,var(--primary)_7%,transparent)]",
      )}
    >
      <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-surface-2">
        <Icon className="size-3.5 text-muted-foreground" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-start justify-between gap-2">
          <span className={cn("text-sm leading-snug", !item.read_at && "font-medium")}>{text.title}</span>
          {!item.read_at && <span className="mt-1.5 size-2 shrink-0 rounded-full bg-primary" aria-label={t.notifications.unread} />}
        </span>
        {text.message && (
          <span className={cn("mt-0.5 block text-xs text-muted-foreground", compact && "line-clamp-2")}>{text.message}</span>
        )}
        {item.created_at && <span className="mt-1 block text-[11px] text-muted-foreground">{formatRelative(item.created_at)}</span>}
      </span>
    </button>
  );
}

/** The bell in the header: unread badge (99+), recent notifications, mark read, and the full list. */
export function NotificationCenter() {
  const { t } = useI18n();
  const router = useRouter();
  const live = useNotificationsLive();
  const [open, setOpen] = useState(false);
  const unread = useUnreadCount(!live).data ?? 0;
  const recent = useNotifications(0, 12, false, open && !live);
  const markRead = useMarkRead();
  const items = recent.data?.items ?? [];

  async function follow(item: AppNotification) {
    if (!item.read_at) await markRead(item.id);
    setOpen(false);
    if (item.link) router.push(item.link);
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="relative" aria-label={t.notifications.title}>
          <Bell className="size-4" />
          {unread > 0 && (
            <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-semibold leading-none text-primary-foreground">
              {unread > 99 ? "99+" : unread}
            </span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(22rem,calc(100vw-2rem))] p-0">
        <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
          <p className="text-sm font-medium">{t.notifications.title}</p>
          <Button variant="ghost" size="sm" className="h-7 text-xs" disabled={!unread} onClick={() => void markRead()}>
            <CheckCheck className="size-3.5" /> {t.notifications.markAllRead}
          </Button>
        </div>
        <div className="max-h-96 space-y-1 overflow-y-auto p-2">
          {!items.length && <p className="p-3 text-sm text-muted-foreground">{t.notifications.empty}</p>}
          {items.map((item) => (
            <NotificationRow key={item.id} item={item} compact onOpen={() => void follow(item)} />
          ))}
        </div>
        <div className="border-t border-border p-2">
          <Button asChild variant="ghost" size="sm" className="w-full">
            <Link href="/notifications" onClick={() => setOpen(false)}>
              {t.notifications.viewAll}
            </Link>
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
