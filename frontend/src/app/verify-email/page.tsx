"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { FormNote, PublicShell, useHashToken } from "@/components/reelforge/public-shell";
import { api, jsonRequest } from "@/lib/api";
import { useDocumentTitle } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { keys } from "@/lib/queries";

/** The emailed verification link (#token=…): works signed in or not, once. */
export default function VerifyEmailPage() {
  const { t } = useI18n();
  const p = t.publicPages.verify;
  useDocumentTitle(p.title);
  const client = useQueryClient();
  const token = useHashToken();
  const sent = useRef(false);
  const [state, setState] = useState<{ email: string } | "failed" | null>(null);

  useEffect(() => {
    if (token === undefined || sent.current) return;
    sent.current = true;
    if (token === null) {
      setState("failed");
      return;
    }
    api<{ email: string }>("account/verify-email", jsonRequest("POST", { token }))
      .then((result) => {
        setState({ email: result.email });
        void client.invalidateQueries({ queryKey: keys.dashboard });
        void client.invalidateQueries({ queryKey: keys.account });
      })
      .catch(() => setState("failed"));
  }, [token, client]);

  return (
    <PublicShell>
      <h1 className="text-2xl font-semibold">{p.title}</h1>
      <div className="mt-6 space-y-4">
        {state === null ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            {p.working}
          </p>
        ) : state === "failed" ? (
          <FormNote tone="warning">{p.failed}</FormNote>
        ) : (
          <FormNote tone="success">{p.done(state.email)}</FormNote>
        )}
        <Button asChild className="w-full"><Link href="/">{p.continue}</Link></Button>
      </div>
    </PublicShell>
  );
}
