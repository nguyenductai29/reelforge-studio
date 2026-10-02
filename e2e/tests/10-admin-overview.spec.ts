import { ADMIN, adminApi, expect, registerApi, signIn, test } from "./helpers";

test("admin overview: the first tab, its figures and what needs attention, each opening the tab that handles it", async ({ page }) => {
  test.setTimeout(120_000);
  const admin = await adminApi();
  // A support request waiting for a reply, which an administrator made high priority.
  const user = await registerApi("overview-user@example.com", "overview-password-1", "Overview Studio");
  const ticket = await user.post("/api/support/tickets", {
    data: { subject: "Overview check", category: "other", description: "Please have a look." } });
  expect(ticket.status(), await ticket.text()).toBe(201);
  const ticketId = (await ticket.json()).id;
  expect((await admin.patch(`/api/admin/support/${ticketId}`, { data: { priority: "high" } })).status()).toBe(200);
  // System-wide figures for administrators only.
  expect((await user.get("/api/admin/overview")).status()).toBe(403);
  const summary = await (await admin.get("/api/admin/overview")).json();
  expect(summary.overview.users).toBeGreaterThanOrEqual(2);
  expect(summary.support.high_priority).toBeGreaterThanOrEqual(1);
  await user.dispose();

  await signIn(page, ADMIN.email, ADMIN.password);
  await page.goto("/admin");
  await expect(page.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
  const overview = page.getByRole("tabpanel");
  // Case-insensitive: the labels are shown in capitals.
  for (const name of [/^users \d/i, /^active users \d/i, /^studios \d/i, /^active plans \d/i, /^paid · 30 days/i,
                      /^jobs · 24 h \d/i, /^system (healthy|warning|critical)/i]) {
    await expect(overview.getByRole("link", { name }).first()).toBeVisible();
  }
  const health = overview.getByRole("region", { name: "System health" });
  for (const name of ["API", "Database", "Workers", "Media disk", "Backups", "Email", "Configuration"]) {
    await expect(health.getByText(name, { exact: true })).toBeVisible();
  }
  // Nothing generated in this run: the section says so instead of showing empty bars.
  await expect(overview.getByRole("region", { name: /^AI usage/ }).getByText("No generation jobs in this period.")).toBeVisible();
  for (const name of ["Payments", "Credits", /^Growth/, /^Publishing/, "Storage", "Support", "Recent admin activity"]) {
    await expect(overview.getByRole("region", { name })).toBeVisible();
  }

  // An attention item opens its tab with its filter: the high-priority tickets.
  const attention = overview.getByRole("region", { name: "Needs attention" });
  await attention.getByRole("link", { name: /High-priority tickets awaiting a reply: \d/ }).click();
  await expect(page.getByRole("tab", { name: "Support" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tabpanel").getByText("Overview check")).toBeVisible();
  await expect(page.getByRole("tabpanel").getByRole("combobox").filter({ hasText: "High" })).toBeVisible();

  // A figure opens its tab too.
  await page.getByRole("tab", { name: "Overview" }).click();
  await page.getByRole("tabpanel").getByRole("link", { name: /^studios \d/i }).click();
  await expect(page.getByRole("tab", { name: "Studios & credits" })).toHaveAttribute("aria-selected", "true");

  // A shared link with a filter opens the tab filtered.
  await page.goto("/admin?tab=payments&status=awaiting_confirmation");
  await expect(page.getByRole("tab", { name: "Payments" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tabpanel").getByRole("combobox").filter({ hasText: "Awaiting confirmation" })).toBeVisible();
});
