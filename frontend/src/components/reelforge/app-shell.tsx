"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  BadgeDollarSign,
  Bot,
  Calendar,
  Clapperboard,
  FolderKanban,
  Home,
  Languages,
  Layers,
  LibraryBig,
  LogOut,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Rss,
  Search,
  Settings,
  Share2,
  ShieldCheck,
  Sparkles,
  Workflow,
  X,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Progress } from "@/components/ui/progress";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import { useErrorToast } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { LOCALES, LOCALE_NAMES, isLocale } from "@/lib/i18n/config";
import type { Dictionary } from "@/lib/i18n/vi";
import { useBilling, useDashboard, useRuns, useUsage } from "@/lib/queries";
import { assetKind, isActiveRun } from "@/lib/studio";
import { StatusBadge } from "./primitives";

type NavKey = Exclude<keyof Dictionary["nav"], "groups">;
type NavItem = { key: NavKey; href: string; icon: LucideIcon };

const navGroups: { heading: keyof Dictionary["nav"]["groups"]; items: NavItem[] }[] = [
  {
    heading: "main",
    items: [
      { key: "home", href: "/", icon: Home },
      { key: "create", href: "/create", icon: Sparkles },
      { key: "projects", href: "/projects", icon: FolderKanban },
      { key: "workflows", href: "/workflows", icon: Workflow },
      { key: "library", href: "/library", icon: LibraryBig },
      { key: "publishing", href: "/publishing", icon: Share2 },
      { key: "calendar", href: "/calendar", icon: Calendar },
    ],
  },
  {
    heading: "ai",
    items: [
      { key: "models", href: "/models", icon: Bot },
      { key: "media", href: "/media", icon: Layers },
    ],
  },
  {
    heading: "workspace",
    items: [
      { key: "channels", href: "/channels", icon: Rss },
      { key: "billing", href: "/billing", icon: BadgeDollarSign },
      { key: "settings", href: "/settings", icon: Settings },
    ],
  },
];

function Logo({ collapsed }: { collapsed?: boolean }) {
  return (
    <Link href="/" className="flex items-center gap-2.5 px-1">
      <span className="brand-fill flex size-8 shrink-0 items-center justify-center rounded-lg">
        <Clapperboard className="size-4" />
      </span>
      {!collapsed && (
        <span className="font-display text-[15px] font-semibold tracking-tight">
          ReelForge <span className="text-muted-foreground">Studio</span>
        </span>
      )}
    </Link>
  );
}

function NavList({ collapsed, onNavigate }: { collapsed?: boolean; onNavigate?: () => void }) {
  const pathname = usePathname();
  const { t } = useI18n();
  const { data } = useDashboard();
  const groups = data?.is_admin
    ? navGroups.map((group) =>
        group.heading === "workspace"
          ? { ...group, items: [...group.items, { key: "admin" as const, href: "/admin", icon: ShieldCheck }] }
          : group,
      )
    : navGroups;

  return (
    <nav aria-label={t.shell.navigation} className="scrollbar-thin flex-1 space-y-6 overflow-y-auto px-3 py-4">
      {groups.map((group) => (
        <div key={group.heading}>
          {!collapsed && (
            <p className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
              {t.nav.groups[group.heading]}
            </p>
          )}
          <div className="space-y-0.5">
            {group.items.map((item) => {
              const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
              const label = t.nav[item.key];
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={onNavigate}
                  title={collapsed ? label : undefined}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "flex items-center gap-3 rounded-lg px-2.5 py-2 text-sm transition-colors",
                    collapsed && "justify-center px-0",
                    active
                      ? "bg-sidebar-accent text-sidebar-accent-foreground"
                      : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
                  )}
                >
                  <item.icon className={cn("size-4 shrink-0", active && "text-primary")} />
                  {!collapsed && <span className="truncate">{label}</span>}
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}

function compactNumber(n: number) {
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n);
}

function CreditsFooter({ collapsed }: { collapsed?: boolean }) {
  const { t, formatNumber, formatDate } = useI18n();
  const usage = useUsage();
  const billing = useBilling();
  const balance = usage.data?.balance ?? 0;
  const subscription = billing.data?.subscription;
  const plan = billing.data?.plans.find((p) => p.code === subscription?.plan_code);
  const planName = plan?.name ?? subscription?.plan_code.toUpperCase() ?? "";
  const monthly = plan?.monthly_credits ?? 0;

  if (collapsed) {
    return (
      <Link
        href="/billing"
        className="border-t border-sidebar-border p-3 text-center text-[10px] text-muted-foreground hover:text-foreground"
      >
        {compactNumber(balance)}
      </Link>
    );
  }
  return (
    <div className="border-t border-sidebar-border p-3">
      <Link href="/billing" className="block rounded-lg border border-border bg-surface p-3 hover:border-border-strong">
        <div className="flex items-center justify-between text-xs">
          <span className="text-muted-foreground">{t.shell.credits}</span>
          <span className="font-medium">{formatNumber(balance)}</span>
        </div>
        <Progress value={monthly > 0 ? Math.min(100, (balance / monthly) * 100) : balance > 0 ? 100 : 0} className="mt-2 h-1.5" />
        {subscription && (
          <p className="mt-2 truncate text-[11px] text-muted-foreground">
            {subscription.ends_at ? t.shell.renews(formatDate(subscription.ends_at), planName) : t.shell.planOnly(planName)}
          </p>
        )}
      </Link>
    </div>
  );
}

function GenerationCenter() {
  const { t, formatRelative } = useI18n();
  const runs = useRuns();
  const { data } = useDashboard();
  const recent = (runs.data ?? []).slice(0, 8);
  const active = (runs.data ?? []).filter(isActiveRun).length;
  const projectTitle = (id: string) => data?.projects.find((p) => p.id === id)?.title ?? t.editor.runs.unknownProject;
  const workflowName = (id: string) => data?.workflows.find((w) => w.id === id)?.name ?? "";

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="relative" aria-label={t.shell.generationCenter}>
          <Activity className="size-4" />
          {active > 0 && <span className="absolute right-1.5 top-1.5 size-2 rounded-full bg-primary" />}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <p className="text-sm font-medium">{t.shell.generationCenter}</p>
          <span className="text-xs text-muted-foreground">{t.shell.activeCount(active)}</span>
        </div>
        <div className="max-h-80 space-y-3 overflow-y-auto p-3">
          {recent.length === 0 && <p className="p-2 text-sm text-muted-foreground">{t.shell.noGenerations}</p>}
          {recent.map((run) => (
            <Link
              key={run.id}
              href={`/workflows/${run.workflow_id}?run=${run.id}`}
              className="block rounded-lg border border-border p-3 transition-colors hover:border-border-strong"
            >
              <div className="flex items-start justify-between gap-2">
                <p className="min-w-0 break-words text-sm leading-snug">{projectTitle(run.project_id)}</p>
                <StatusBadge status={run.status} label={t.status.run[run.status]} />
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {workflowName(run.workflow_id)} · {formatRelative(run.created_at)}
              </p>
              {isActiveRun(run) && (
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-primary/20">
                  <div className="h-full w-1/3 animate-pulse rounded-full bg-primary" />
                </div>
              )}
            </Link>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function SearchBox() {
  const { t } = useI18n();
  const router = useRouter();
  const { data } = useDashboard();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const results = useMemo(() => {
    const q = query.trim().toLocaleLowerCase();
    if (!q || !data) return [];
    const match = (text: string) => text.toLocaleLowerCase().includes(q);
    return [
      ...data.projects
        .filter((p) => match(p.title) || match(p.topic))
        .slice(0, 5)
        .map((p) => ({ group: t.shell.searchGroups.projects, label: p.title, href: `/projects/${p.id}`, icon: FolderKanban })),
      ...data.workflows
        .filter((w) => match(w.name))
        .slice(0, 4)
        .map((w) => ({ group: t.shell.searchGroups.workflows, label: w.name, href: `/workflows/${w.id}`, icon: Workflow })),
      ...data.assets
        .filter((a) => match(a.filename))
        .slice(0, 5)
        .map((a) => ({
          group: t.shell.searchGroups.media,
          label: a.filename,
          href: `/media?asset=${a.id}`,
          icon: assetKind(a.content_type) === "video" ? Clapperboard : Layers,
        })),
    ];
  }, [query, data, t]);

  const go = (href: string) => {
    setQuery("");
    setOpen(false);
    inputRef.current?.blur();
    router.push(href);
  };

  return (
    <div className="relative hidden max-w-sm flex-1 sm:block">
      <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        ref={inputRef}
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && results[0]) go(results[0].href);
          if (e.key === "Escape") {
            setQuery("");
            inputRef.current?.blur();
          }
        }}
        placeholder={t.shell.searchPlaceholder}
        aria-label={t.shell.searchPlaceholder}
        className="h-9 bg-surface pl-9"
      />
      {open && query.trim() && (
        <div className="absolute inset-x-0 top-11 z-40 overflow-hidden rounded-lg border border-border bg-popover shadow-[var(--shadow-panel)]">
          {results.length === 0 ? (
            <p className="px-3 py-3 text-sm text-muted-foreground">{t.shell.searchEmpty}</p>
          ) : (
            <ul className="max-h-80 overflow-y-auto py-1">
              {results.map((result, index) => (
                <li key={`${result.href}-${index}`}>
                  <button
                    type="button"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => go(result.href)}
                    className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm hover:bg-surface-2"
                  >
                    <result.icon className="size-3.5 shrink-0 text-primary" />
                    <span className="min-w-0 flex-1 truncate">{result.label}</span>
                    <span className="shrink-0 text-[11px] text-muted-foreground">{result.group}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function AccountMenu() {
  const { t, locale, setLocale } = useI18n();
  const { data } = useDashboard();
  const client = useQueryClient();
  const router = useRouter();
  const showError = useErrorToast();
  const email = data?.user.email ?? "";
  const initials = (email.split("@")[0] ?? "").replace(/[^a-zA-Z0-9]/g, "").slice(0, 2).toUpperCase() || "RF";

  async function signOut() {
    try {
      await api("logout", { method: "POST" });
      router.push("/");
      // Resetting refetches the session query, which then shows the sign-in screen.
      await client.resetQueries();
    } catch (error) {
      showError(error);
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={t.shell.account}
          className="ml-1 flex size-8 items-center justify-center rounded-full bg-surface-2 text-xs font-medium hover:ring-2 hover:ring-ring/40"
        >
          {initials}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuLabel className="space-y-0.5">
          <p className="truncate text-sm">{email}</p>
          <p className="truncate text-xs font-normal text-muted-foreground">
            {data?.workspace.name}
            {data?.is_admin ? ` · ${t.shell.systemAdmin}` : ""}
          </p>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>
            <Languages className="size-4" />
            {t.shell.language}
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            <DropdownMenuRadioGroup value={locale} onValueChange={(value) => isLocale(value) && setLocale(value)}>
              {LOCALES.map((code) => (
                <DropdownMenuRadioItem key={code} value={code}>
                  {LOCALE_NAMES[code]}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuItem asChild>
          <Link href="/settings">
            <Settings className="size-4" />
            {t.nav.settings}
          </Link>
        </DropdownMenuItem>
        {data?.is_admin && (
          <DropdownMenuItem asChild>
            <Link href="/admin">
              <ShieldCheck className="size-4" />
              {t.nav.admin}
            </Link>
          </DropdownMenuItem>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => void signOut()}>
          <LogOut className="size-4" />
          {t.shell.signOut}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { t } = useI18n();
  const fullBleed = pathname.startsWith("/workflows/");
  const [collapsed, setCollapsed] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);

  return (
    <div className="min-h-screen bg-background">
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-30 hidden flex-col border-r border-sidebar-border bg-sidebar lg:flex",
          collapsed ? "w-[68px]" : "w-64",
        )}
      >
        <div
          className={cn(
            "flex h-14 items-center border-b border-sidebar-border px-3",
            collapsed ? "justify-center" : "justify-between",
          )}
        >
          <Logo collapsed={collapsed} />
          {!collapsed && (
            <Button variant="ghost" size="icon" onClick={() => setCollapsed(true)} aria-label={t.shell.collapse}>
              <PanelLeftClose className="size-4" />
            </Button>
          )}
        </div>
        {collapsed && (
          <Button
            variant="ghost"
            size="icon"
            className="mx-auto mt-2"
            onClick={() => setCollapsed(false)}
            aria-label={t.shell.expand}
          >
            <PanelLeftOpen className="size-4" />
          </Button>
        )}
        <NavList collapsed={collapsed} />
        <CreditsFooter collapsed={collapsed} />
      </aside>

      <Sheet open={mobileNav} onOpenChange={setMobileNav}>
        <SheetContent side="left" className="w-72 bg-sidebar p-0 [&>button]:hidden">
          <SheetTitle className="sr-only">{t.shell.navigation}</SheetTitle>
          <div className="flex h-14 items-center justify-between border-b border-sidebar-border px-3">
            <Logo />
            <Button variant="ghost" size="icon" onClick={() => setMobileNav(false)} aria-label={t.shell.closeNav}>
              <X className="size-4" />
            </Button>
          </div>
          <div className="flex h-[calc(100%-3.5rem)] flex-col">
            <NavList onNavigate={() => setMobileNav(false)} />
            <CreditsFooter />
          </div>
        </SheetContent>
      </Sheet>

      <div className={cn(collapsed ? "lg:pl-[68px]" : "lg:pl-64")}>
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-background/85 px-4 backdrop-blur">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setMobileNav(true)}
            aria-label={t.shell.openNav}
          >
            <Menu className="size-4" />
          </Button>
          <SearchBox />
          <div className="ml-auto flex items-center gap-1.5">
            <GenerationCenter />
            <AccountMenu />
          </div>
        </header>

        {fullBleed ? (
          <main>{children}</main>
        ) : (
          <main className="mx-auto w-full max-w-[1400px] px-4 py-6 sm:px-6 lg:px-8">{children}</main>
        )}
      </div>
    </div>
  );
}
