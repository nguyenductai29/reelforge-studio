"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { Facebook, Music2, Youtube, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";
import { statusTone } from "@/lib/studio";

export type Platform = "youtube" | "tiktok" | "facebook";

export const platformLabel: Record<Platform, string> = {
  youtube: "YouTube",
  tiktok: "TikTok",
  facebook: "Facebook",
};

const platformIcon: Record<Platform, LucideIcon> = {
  youtube: Youtube,
  tiktok: Music2,
  facebook: Facebook,
};

const platformColor: Record<Platform, string> = {
  youtube: "text-yt",
  tiktok: "text-tt",
  facebook: "text-fb",
};

export function PlatformBadge({
  platform,
  withLabel = false,
  className,
}: {
  platform: Platform;
  withLabel?: boolean;
  className?: string;
}) {
  const Icon = platformIcon[platform];
  return (
    <span
      title={platformLabel[platform]}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border border-border bg-surface-2 px-1.5 py-1 text-xs",
        className,
      )}
    >
      <Icon className={cn("size-3.5", platformColor[platform])} />
      {withLabel && <span className="pr-0.5">{platformLabel[platform]}</span>}
    </span>
  );
}

export function PlatformIcon({ platform, className }: { platform: Platform; className?: string }) {
  const Icon = platformIcon[platform];
  return <Icon className={cn("size-4", platformColor[platform], className)} />;
}

const toneStyle: Record<string, string> = {
  draft: "bg-muted text-muted-foreground",
  generating: "bg-[color-mix(in_oklab,var(--warning)_18%,transparent)] text-warning",
  review: "bg-[color-mix(in_oklab,var(--info)_18%,transparent)] text-info",
  ready: "bg-[color-mix(in_oklab,var(--primary)_18%,transparent)] text-primary",
  published: "bg-[color-mix(in_oklab,var(--success)_18%,transparent)] text-success",
  scheduled: "bg-[color-mix(in_oklab,var(--info)_18%,transparent)] text-info",
  publishing: "bg-[color-mix(in_oklab,var(--warning)_18%,transparent)] text-warning",
  failed: "bg-[color-mix(in_oklab,var(--destructive)_18%,transparent)] text-destructive",
  queued: "bg-muted text-muted-foreground",
  processing: "bg-[color-mix(in_oklab,var(--warning)_18%,transparent)] text-warning",
  completed: "bg-[color-mix(in_oklab,var(--success)_18%,transparent)] text-success",
  connected: "bg-[color-mix(in_oklab,var(--success)_18%,transparent)] text-success",
  "not connected": "bg-muted text-muted-foreground",
  "needs attention": "bg-[color-mix(in_oklab,var(--warning)_18%,transparent)] text-warning",
};

/** `status` picks the colour (any backend or display status); `label` is the translated text. */
export function StatusBadge({ status, label, className }: { status: string; label: string; className?: string }) {
  const tone = statusTone[status] ?? status;
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
        toneStyle[tone] ?? "bg-muted text-muted-foreground",
        className,
      )}
    >
      <span className="size-1.5 rounded-full bg-current" />
      {label}
    </span>
  );
}

/** ``compact``: the size of the admin console's header, for pages that fit the window (Settings). */
export function PageHeader({ title, subtitle, actions, compact = false }: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="max-w-2xl">
        <h1 className={compact ? "text-xl font-semibold" : "text-2xl font-semibold sm:text-3xl"}>{title}</h1>
        {subtitle && <p className={compact ? "mt-0.5 text-xs text-muted-foreground" : "mt-2 text-sm text-muted-foreground"}>{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function SectionTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-4 flex items-center justify-between gap-4">
      <h2 className="text-base font-semibold tracking-tight">{children}</h2>
      {action}
    </div>
  );
}

export function SectionLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link href={href} className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
      {children}
    </Link>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("panel flex flex-col items-center px-6 py-14 text-center", className)}>
      <div className="mb-4 flex size-12 items-center justify-center rounded-xl border border-border bg-surface-2">
        <Icon className="size-5 text-muted-foreground" />
      </div>
      <p className="font-medium">{title}</p>
      <p className="mt-1 max-w-sm text-sm text-muted-foreground">{description}</p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function OptionChips({
  options,
  value,
  onChange,
  multi = false,
  disabled = false,
}: {
  options: { value: string; label: string }[] | string[];
  value: string[];
  onChange: (next: string[]) => void;
  multi?: boolean;
  disabled?: boolean;
}) {
  const items = options.map((option) => (typeof option === "string" ? { value: option, label: option } : option));
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((option) => {
        const active = value.includes(option.value);
        return (
          <button
            key={option.value}
            type="button"
            disabled={disabled}
            aria-pressed={active}
            onClick={() =>
              onChange(
                multi ? (active ? value.filter((v) => v !== option.value) : [...value, option.value]) : [option.value],
              )
            }
            className={cn(
              "rounded-lg border px-3 py-1.5 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-60",
              active
                ? "border-primary/60 bg-[color-mix(in_oklab,var(--primary)_16%,transparent)] text-primary"
                : "border-border bg-surface-2 text-muted-foreground hover:text-foreground",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export function FieldLabel({ children, htmlFor }: { children: ReactNode; htmlFor?: string }) {
  const className = "mb-2 block text-xs font-medium uppercase tracking-wider text-muted-foreground";
  return htmlFor ? (
    <label htmlFor={htmlFor} className={className}>
      {children}
    </label>
  ) : (
    <p className={className}>{children}</p>
  );
}

export function FilterPills<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (next: T) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
          className={cn(
            "rounded-lg border px-3 py-1.5 text-sm transition-colors",
            value === option.value
              ? "border-primary/60 bg-[color-mix(in_oklab,var(--primary)_16%,transparent)] text-primary"
              : "border-border bg-surface-2 text-muted-foreground hover:text-foreground",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function StatCard({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="panel p-5">
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold">{value}</p>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** A form row laid out like the design's settings toggles. */
export function SettingRow({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="min-w-0">
        <div className="text-sm">{label}</div>
        {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
      </div>
      {children}
    </div>
  );
}
