"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, jsonRequest } from "./api";
import { useErrorToast } from "./errors";
import { useI18n } from "./i18n";
import { keys } from "./queries";
import type { Workflow } from "./types";
import { templateById, type TemplateId } from "./workflow";

export function useDocumentTitle(page: string | undefined) {
  const { t } = useI18n();
  useEffect(() => {
    document.title = page ? t.meta.title(page) : "ReelForge Studio";
  }, [page, t]);
}

/** Creates a workflow from a runnable template and opens it on the canvas. */
export function useCreateFromTemplate() {
  const router = useRouter();
  const client = useQueryClient();
  const showError = useErrorToast();
  const { t } = useI18n();
  const [pending, setPending] = useState<TemplateId | null>(null);

  async function create(id: TemplateId) {
    const template = templateById(id);
    if (!template.graph || pending) return;
    setPending(id);
    try {
      const workflow = await api<Workflow>("workflows", jsonRequest("POST", { name: t.templates[id].name }));
      await api(`workflows/${encodeURIComponent(workflow.id)}`, jsonRequest("PUT", template.graph));
      await client.invalidateQueries({ queryKey: keys.dashboard });
      toast.success(t.create.created);
      router.push(`/workflows/${workflow.id}`);
    } catch (error) {
      showError(error);
    } finally {
      setPending(null);
    }
  }

  return { create, pending };
}

/** One-off query parameters (payment returns, OAuth results) read after hydration. */
export function useSearchParam(name: string) {
  const [value, setValue] = useState<string | null>(null);
  useEffect(() => {
    setValue(new URLSearchParams(window.location.search).get(name));
  }, [name]);
  return value;
}
