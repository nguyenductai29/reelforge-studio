/** What the backend adds to a 422 about node settings, e.g. `{ code: "invalid_language", field: "language" }`. */
export type ErrorCode = { code: string; field: string | null; nodeId: string | null };

/** A failed API call; `detail` is the backend's message, or null when it sent none. Since Phase 24 every error
 * body also has a stable `code` and the `request_id` an administrator can find in the logs. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string | null,
    readonly error: ErrorCode | null = null,
    readonly code: string | null = null,
    readonly requestId: string | null = null,
  ) {
    super(detail ?? `Request failed (${status})`);
  }
}

function parseError(status: number, data: { detail?: unknown; code?: unknown; request_id?: unknown }): ApiError {
  const detail = data.detail;
  const code = typeof data.code === "string" ? data.code : null;
  const requestId = typeof data.request_id === "string" ? data.request_id : null;
  if (typeof detail === "string") return new ApiError(status, detail, null, code, requestId);
  if (detail && typeof detail === "object" && "code" in detail && typeof detail.code === "string") {
    const d = detail as { code: string; message?: unknown; field?: unknown; node_id?: unknown };
    return new ApiError(status, typeof d.message === "string" ? d.message : null, {
      code: d.code,
      field: typeof d.field === "string" ? d.field : null,
      nodeId: typeof d.node_id === "string" ? d.node_id : null,
    }, code, requestId);
  }
  return new ApiError(status, null, null, code, requestId);
}

export async function api<T>(endpoint: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api/${endpoint}`, { credentials: "same-origin", cache: "no-store", ...init });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw parseError(res.status, data);
  return data as T;
}

export function jsonRequest(method: "POST" | "PUT" | "PATCH", body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const assetUrl = (id: string) => `/api/assets/${encodeURIComponent(id)}`;
