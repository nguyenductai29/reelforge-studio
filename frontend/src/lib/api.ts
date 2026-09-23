export async function api<T>(endpoint: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api/${endpoint}`, { credentials: "same-origin", cache: "no-store", ...init });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Yêu cầu thất bại (${res.status})`);
  return data as T;
}
export function jsonRequest(method: "POST" | "PUT", body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}
