"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

export default function NotFound() {
  const { t } = useI18n();
  useDocumentTitle(t.notFound.title);
  return (
    <div className="flex min-h-[60vh] items-center justify-center px-4">
      <div className="max-w-md text-center">
        <h1 className="text-7xl font-bold text-foreground">404</h1>
        <h2 className="mt-4 text-xl font-semibold text-foreground">{t.notFound.title}</h2>
        <p className="mt-2 text-sm text-muted-foreground">{t.notFound.description}</p>
        <Button asChild className="mt-6">
          <Link href="/">{t.notFound.home}</Link>
        </Button>
      </div>
    </div>
  );
}
