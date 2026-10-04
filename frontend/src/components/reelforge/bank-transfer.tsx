"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import type { BankTransfer } from "@/lib/types";
import { cn } from "@/lib/utils";

/** The QR and the same facts as text, for apps that cannot scan; shared by the admin preview and Billing. */
export function TransferCard({ transfer, compact = false }: { transfer: BankTransfer; compact?: boolean }) {
  const { t, formatMoney } = useI18n();
  const b = t.billing.transfer;
  const rows: [string, string, boolean][] = [
    [b.bank, transfer.bank_name || transfer.bank_bin, false],
    [b.account, transfer.account_number, true],
    [b.holder, transfer.account_name, false],
    [b.amount, formatMoney(transfer.amount_vnd), false],
    [b.content, transfer.content, true],
  ];
  return (
    <div className={cn("grid gap-3", compact ? "" : "sm:grid-cols-[auto_1fr] sm:items-start")}>
      {/* eslint-disable-next-line @next/next/no-img-element -- an inline SVG data URI drawn by the server */}
      <img src={transfer.qr} alt={b.qrAlt(transfer.content)} width={compact ? 168 : 208} height={compact ? 168 : 208}
           className="mx-auto rounded-lg bg-white p-1" />
      <dl className="space-y-1.5 text-sm">
        {rows.map(([label, value, copy]) => (
          <div key={label} className="flex items-center justify-between gap-2 border-b border-border/60 pb-1 last:border-0">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="flex items-center gap-1 font-medium">
              <span className={cn(copy && "font-mono")}>{value}</span>
              {copy && <CopyButton value={value} />}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function CopyButton({ value }: { value: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  return (
    <Button type="button" variant="ghost" size="icon" className="size-6 shrink-0" aria-label={t.admin.gateways.copy}
            onClick={() => {
              void navigator.clipboard?.writeText(value);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}>
      {copied ? <Check className="size-3 text-success" /> : <Copy className="size-3" />}
    </Button>
  );
}
