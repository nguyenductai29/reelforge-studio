/** A failed API call; `detail` is the backend's message, or null when it sent none. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string | null,
  ) {
    super(detail ?? `Request failed (${status})`);
  }
}

export async function api<T>(endpoint: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api/${endpoint}`, { credentials: "same-origin", cache: "no-store", ...init });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, typeof data.detail === "string" ? data.detail : null);
  return data as T;
}

export function jsonRequest(method: "POST" | "PUT" | "PATCH", body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const assetUrl = (id: string) => `/api/assets/${encodeURIComponent(id)}`;
