"use client";

import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader, PlatformIcon, StatusBadge, platformLabel } from "@/components/reelforge/primitives";
import { ScheduleDialog } from "@/components/reelforge/publication-actions";
import { useDocumentTitle } from "@/lib/hooks";
import { toDate, useI18n } from "@/lib/i18n";
import { useCalendarPublications } from "@/lib/queries";
import type { Publication } from "@/lib/types";
import { cn } from "@/lib/utils";

const dayKey = (date: Date) => `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
const addDays = (date: Date, days: number) => new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);
/** Monday-based weekday index, 0–6. */
const weekday = (date: Date) => (date.getDay() + 6) % 7;
/** When a publication shows on the calendar: its scheduled time, else when it went out, else when it was queued
 * (the same order the server uses to select a date range). */
const when = (p: Publication) => p.scheduled_for ?? p.published_at ?? p.created_at;

export default function CalendarPage() {
  const { t, formatDate } = useI18n();
  const [scheduling, setScheduling] = useState<{ publication: Publication; mode: "reschedule" | "cancel" } | null>(null);
  useDocumentTitle(t.nav.calendar);
  const today = new Date();
  const [cursor, setCursor] = useState(() => new Date(today.getFullYear(), today.getMonth(), 1));
  const gridStart = addDays(cursor, -weekday(cursor));
  const daysInMonth = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0).getDate();
  const cells = Math.ceil((weekday(cursor) + daysInMonth) / 7) * 7;
  // Only the visible grid is loaded (local midnights, sent as UTC instants); the week view lies inside it.
  const range = useCalendarPublications(gridStart.toISOString(), addDays(gridStart, cells).toISOString());
  const publications = useMemo(() => range.data ?? [], [range.data]);

  const byDay = useMemo(() => {
    const map = new Map<string, Publication[]>();
    for (const p of publications) {
      if (p.state === "cancelled") continue;
      const key = dayKey(toDate(when(p)));
      map.set(key, [...(map.get(key) ?? []), p]);
    }
    return map;
  }, [publications]);

  const days = Array.from({ length: cells }, (_, i) => addDays(gridStart, i));
  const weekdayNames = Array.from({ length: 7 }, (_, i) => formatDate(addDays(gridStart, i), { weekday: "short" }));
  const sameMonth = today.getFullYear() === cursor.getFullYear() && today.getMonth() === cursor.getMonth();
  const weekStart = addDays(sameMonth ? today : cursor, -weekday(sameMonth ? today : cursor));
  const week = Array.from({ length: 7 }, (_, i) => addDays(weekStart, i));
  const monthItems = publications
    .filter((p) => {
      const d = toDate(when(p));
      return d.getFullYear() === cursor.getFullYear() && d.getMonth() === cursor.getMonth();
    })
    .sort((a, b) => toDate(when(a)).getTime() - toDate(when(b)).getTime());
  const time = (p: Publication) => formatDate(when(p), { hour: "2-digit", minute: "2-digit" });
  const scheduledCount = monthItems.filter((p) => p.state === "scheduled").length;

  const Item = ({ p, compact }: { p: Publication; compact?: boolean }) => (
    <div
      className={cn(
        "rounded-md border bg-surface-2 leading-tight",
        p.state === "scheduled" ? "border-info/50" : "border-border",
        compact ? "p-1.5 text-[10px]" : "mt-2 p-2 text-[11px]",
      )}
      title={p.title}
    >
      <div className="flex items-center gap-1">
        <PlatformIcon platform={p.channel} className="size-3" />
        <span className="text-muted-foreground">{time(p)}</span>
      </div>
      <p className="mt-0.5 truncate">{p.title}</p>
    </div>
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.calendar.title}
        subtitle={`${t.calendar.subtitle(formatDate(cursor, { month: "long", year: "numeric" }))}${
          scheduledCount ? ` · ${t.calendar.scheduledCount(scheduledCount)}` : ""
        }`}
        actions={
          <div className="flex items-center gap-1">
            <Button
              variant="outline"
              size="icon"
              aria-label={t.calendar.previous}
              onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}
            >
              <ChevronLeft className="size-4" />
            </Button>
            <Button variant="outline" size="sm" onClick={() => setCursor(new Date(today.getFullYear(), today.getMonth(), 1))}>
              {t.calendar.today}
            </Button>
            <Button
              variant="outline"
              size="icon"
              aria-label={t.calendar.next}
              onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}
            >
              <ChevronRight className="size-4" />
            </Button>
          </div>
        }
      />

      <Tabs defaultValue="month">
        <TabsList>
          <TabsTrigger value="month">{t.calendar.tabs.month}</TabsTrigger>
          <TabsTrigger value="week">{t.calendar.tabs.week}</TabsTrigger>
          <TabsTrigger value="list">{t.calendar.tabs.list}</TabsTrigger>
        </TabsList>

        <TabsContent value="month" className="mt-5">
          <div className="panel overflow-x-auto p-2">
            <div className="min-w-[560px]">
              <div className="grid grid-cols-7 gap-1 pb-2 text-center text-[11px] uppercase tracking-wider text-muted-foreground">
                {weekdayNames.map((d) => (
                  <span key={d}>{d}</span>
                ))}
              </div>
              <div className="grid grid-cols-7 gap-1">
                {days.map((day) => {
                  const items = byDay.get(dayKey(day)) ?? [];
                  const isToday = dayKey(day) === dayKey(today);
                  return (
                    <div
                      key={dayKey(day)}
                      className={cn(
                        "min-h-[88px] rounded-lg border border-border bg-surface p-1.5",
                        day.getMonth() !== cursor.getMonth() && "opacity-40",
                        isToday && "border-primary/50",
                      )}
                    >
                      <p className={cn("mb-1 text-[11px] text-muted-foreground", isToday && "font-semibold text-primary")}>
                        {day.getDate()}
                      </p>
                      <div className="space-y-1">
                        {items.slice(0, 3).map((p) => (
                          <Item key={p.id} p={p} compact />
                        ))}
                        {items.length > 3 && <p className="text-[10px] text-muted-foreground">+{items.length - 3}</p>}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="week" className="mt-5">
          <div className="grid gap-2 sm:grid-cols-7">
            {week.map((day) => (
              <div key={dayKey(day)} className="panel min-h-[160px] p-3">
                <p className="text-xs text-muted-foreground">{formatDate(day, { weekday: "short", day: "numeric" })}</p>
                {(byDay.get(dayKey(day)) ?? []).map((p) => (
                  <Item key={p.id} p={p} />
                ))}
              </div>
            ))}
          </div>
        </TabsContent>

        <TabsContent value="list" className="mt-5">
          <div className="panel divide-y divide-border">
            {monthItems.length === 0 && <p className="p-4 text-sm text-muted-foreground">{t.calendar.empty}</p>}
            {monthItems.map((p) => (
              <div key={p.id} className="flex flex-wrap items-center gap-3 p-4 text-sm">
                <span className="w-20 text-muted-foreground">{formatDate(when(p), { month: "short", day: "numeric" })}</span>
                <span className="w-14 text-muted-foreground">{time(p)}</span>
                <span className="min-w-0 flex-1 truncate font-medium">{p.title}</span>
                <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <PlatformIcon platform={p.channel} />
                  {platformLabel[p.channel]}
                </span>
                <StatusBadge status={p.state} label={t.status.publication[p.state]} />
                {p.can_reschedule && (
                  <Button variant="ghost" size="sm" onClick={() => setScheduling({ publication: p, mode: "reschedule" })}>
                    {t.calendar.reschedule}
                  </Button>
                )}
                {p.can_cancel && (
                  <Button variant="ghost" size="sm" onClick={() => setScheduling({ publication: p, mode: "cancel" })}>
                    {t.calendar.cancel}
                  </Button>
                )}
              </div>
            ))}
          </div>
        </TabsContent>
      </Tabs>
      <ScheduleDialog
        publication={scheduling?.publication ?? null}
        mode={scheduling?.mode ?? "reschedule"}
        onClose={() => setScheduling(null)}
      />
    </div>
  );
}
