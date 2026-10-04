"use client";

import { useCallback } from "react";
import { toast } from "sonner";
import { ApiError } from "./api";
import { useI18n } from "./i18n";
import type { Dictionary } from "./i18n/vi";

export function errorText(error: unknown, t: Dictionary): string {
  if (error instanceof ApiError) {
    // A server error never shows its raw body: a sentence and the request ID to quote to an administrator.
    if (error.status >= 500 && !(error.detail && t.errors.server[error.detail])) return t.errors.internal(error.requestId);
    if (error.error) {
      return t.errors.codes[error.error.code] ?? t.config.errors[error.error.code] ?? error.detail ?? t.errors.validation;
    }
    if (error.detail) return t.errors.server[error.detail] ?? error.detail;
    if (error.code && t.errors.codes[error.code]) return t.errors.codes[error.code];
    return error.status === 422 ? t.errors.validation : t.errors.requestFailed(error.status);
  }
  // fetch() rejects with a TypeError when the API cannot be reached at all.
  if (error instanceof TypeError) return t.errors.network;
  return error instanceof Error && error.message ? error.message : t.errors.generic;
}

export function useErrorToast() {
  const { t } = useI18n();
  return useCallback((error: unknown) => toast.error(errorText(error, t)), [t]);
}
