import type { Dashboard, Permission } from "./types";

/**
 * Whether the signed-in member's role allows an action in the active workspace (Phase 23). The API enforces
 * every permission itself; this only hides what it would refuse. An API from before Phase 23 sends no
 * permissions: everything is shown, as before.
 */
export function can(dashboard: Pick<Dashboard, "permissions"> | undefined, permission: Permission): boolean {
  if (!dashboard?.permissions) return true;
  return dashboard.permissions.includes(permission);
}
