"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Bell, CheckCheck } from "lucide-react";
import { QueryError } from "@/components/reelforge/query-state";
import { Button } from "@/components/ui/button";
import { EmptyState, FilterPills, PageHeader } from "@/components/reelforge/primitives";
import { NotificationRow, useMarkRead, useNotificationsLive } from "@/components/reelforge/notifications";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useNotifications } from "@/lib/queries";
import type { AppNotification } from "@/lib/types";

const PAGE = 20;

export default function NotificationsPage() {
  const { t } = useI18n();
  useDocumentTitle(t.notifications.title);
  const router = useRouter();
  const live = useNotificationsLive();
  const [filter, setFilter] = useState<"all" | "unread">("all");
  const [offset, setOffset] = useState(0);
  const page = useNotifications(offset, PAGE, filter === "unread", !live);
  const markRead = useMarkRead();
  const items = page.data?.items ?? [];
  const total = page.data?.total ?? 0;

  async function open(item: AppNotification) {
    if (!item.read_at) await markRead(item.id);
    if (item.link) router.push(item.link);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.notifications.title}
        subtitle={t.notifications.subtitle}
        actions={
          <Button variant="outline" disabled={!page.data?.unread} onClick={() => void markRead()}>
            <CheckCheck className="size-4" /> {t.notifications.markAllRead}
          </Button>
        }
      />
      <FilterPills
        value={filter}
        onChange={(value) => {
          setFilter(value);
          setOffset(0);
        }}
        options={[
          { value: "all", label: t.notifications.all },
          { value: "unread", label: t.notifications.unreadCount(page.data?.unread ?? 0) },
        ]}
      />
      {page.isError && !items.length ? (
        <QueryError error={page.error} onRetry={() => void page.refetch()} />
      ) : !items.length && !page.isPending ? (
        <EmptyState icon={Bell} title={t.notifications.empty} description={t.notifications.emptyHint} />
      ) : (
        <div className="panel space-y-1 p-2">
          {items.map((item) => (
            <NotificationRow key={item.id} item={item} onOpen={() => void open(item)} />
          ))}
        </div>
      )}
      {total > PAGE && (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>{t.table.range(String(offset + 1), String(Math.min(offset + PAGE, total)), String(total))}</span>
          <div className="flex gap-1.5">
            <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              {t.table.previous}
            </Button>
            <Button variant="outline" size="sm" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
              {t.table.next}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
