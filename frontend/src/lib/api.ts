/** What the backend adds to a 422 about node settings, e.g. `{ code: "invalid_language", field: "language" }`. */
export type ErrorCode = { code: string; field: string | null; nodeId: string | null };

/** A failed API call; `detail` is the backend's message, or null when it sent none. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string | null,
    readonly error: ErrorCode | null = null,
  ) {
    super(detail ?? `Request failed (${status})`);
  }
}

function parseError(status: number, detail: unknown): ApiError {
  if (typeof detail === "string") return new ApiError(status, detail);
  if (detail && typeof detail === "object" && "code" in detail && typeof detail.code === "string") {
    const d = detail as { code: string; message?: unknown; field?: unknown; node_id?: unknown };
    return new ApiError(status, typeof d.message === "string" ? d.message : null, {
      code: d.code,
      field: typeof d.field === "string" ? d.field : null,
      nodeId: typeof d.node_id === "string" ? d.node_id : null,
    });
  }
  return new ApiError(status, null);
}

export async function api<T>(endpoint: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api/${endpoint}`, { credentials: "same-origin", cache: "no-store", ...init });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw parseError(res.status, data.detail);
  return data as T;
}

export function jsonRequest(method: "POST" | "PUT" | "PATCH", body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const assetUrl = (id: string) => `/api/assets/${encodeURIComponent(id)}`;
