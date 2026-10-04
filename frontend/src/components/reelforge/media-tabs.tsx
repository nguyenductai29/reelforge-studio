"use client";

import Link from "next/link";
import { Clapperboard, Images } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** Media: the studio's files, and the temporary movie sources a Movie Review or Recap is made from. */
export function MediaTabs({ active }: { active: "files" | "movies" }) {
  const { t } = useI18n();
  const tabs = [
    { key: "files", href: "/media", label: t.movieSources.filesTab, Icon: Images },
    { key: "movies", href: "/media/movie-sources", label: t.movieSources.tab, Icon: Clapperboard },
  ] as const;
  return (
    <nav className="flex gap-1 border-b border-border" aria-label={t.media.title}>
      {tabs.map(({ key, href, label, Icon }) => (
        <Link
          key={key}
          href={href}
          aria-current={active === key ? "page" : undefined}
          className={cn(
            "-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm",
            active === key
              ? "border-primary font-medium text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground",
          )}
        >
          <Icon className="size-4" /> {label}
        </Link>
      ))}
    </nav>
  );
}
