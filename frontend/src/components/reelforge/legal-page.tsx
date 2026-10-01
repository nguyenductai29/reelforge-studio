"use client";

import Link from "next/link";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useAuthStatus } from "@/lib/queries";
import { FormNote, PublicShell } from "./public-shell";

/** /terms and /privacy: the operator's template (every [bracketed] item to fill in), marked as needing legal review. */
export function LegalPage({ kind }: { kind: "terms" | "privacy" }) {
  const { t } = useI18n();
  const page = t.legal[kind];
  useDocumentTitle(page.title);
  const version = useAuthStatus().data?.terms_version;

  return (
    <PublicShell wide>
      <article className="space-y-5">
        <header>
          <h1 className="text-2xl font-semibold">{page.title}</h1>
          {version && <p className="mt-1 text-xs text-muted-foreground">{t.legal.updated(version)}</p>}
        </header>
        <FormNote tone="warning">{t.legal.templateNotice}</FormNote>
        {page.sections.map((section) => (
          <section key={section.heading}>
            <h2 className="text-base font-semibold">{section.heading}</h2>
            <p className="mt-1.5 whitespace-pre-line text-sm leading-relaxed text-muted-foreground">{section.body}</p>
          </section>
        ))}
        <p className="border-t border-border pt-4 text-sm">
          <Link href={kind === "terms" ? "/privacy" : "/terms"} className="text-primary hover:underline">
            {kind === "terms" ? t.legal.privacy.title : t.legal.terms.title}
          </Link>
        </p>
      </article>
    </PublicShell>
  );
}
