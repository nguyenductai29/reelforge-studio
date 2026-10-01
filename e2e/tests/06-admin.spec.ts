import { ADMIN, adminApi, apiAs, expect, registerApi, signIn, test } from "./helpers";

const TABS = ["Users", "Studios & credits", "Plans", "Payments", "Support", "Credit reconciliation", "Operations",
              "Verification", "System settings", "Audit log"];

test("every administration tab opens without an error", async ({ page }) => {
  await adminApi();
  await signIn(page, ADMIN.email, ADMIN.password);
  await page.goto("/admin");
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  page.on("response", (response) => {
    if (response.url().includes("/api/") && response.status() >= 500) failures.push(`${response.status()} ${response.url()}`);
  });
  for (const name of TABS) {
    await page.getByRole("tab", { name }).click();
    await expect(page.getByRole("tab", { name })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("tabpanel")).toBeVisible();
    await expect(page.getByRole("tabpanel").getByText(/Internal Server Error|Traceback|\{"detail"/)).toHaveCount(0);
  }
  expect(failures).toEqual([]);

  // The audit log: the sign-ins of this run, filtered on the server.
  await page.getByRole("tab", { name: "Audit log" }).click();
  await expect(page.getByRole("tabpanel").getByText("Sign-in").first()).toBeVisible();
  await page.getByPlaceholder("Search by email").fill("buyer@example.com");
  await expect(page.getByRole("tabpanel").getByText("buyer@example.com").first()).toBeVisible();
  await expect(page.getByRole("tabpanel").getByText("admin@example.com")).toHaveCount(0);
});

test("a member who is not a system administrator gets no admin data", async ({ page }) => {
  const member = await registerApi("not-admin@example.com", "not-admin-password", "Member Studio");
  expect((await member.get("/api/admin/users")).status()).toBe(403);
  expect((await member.get("/api/admin/audit")).status()).toBe(403);
  expect((await member.get("/api/admin/system-config")).status()).toBe(403);
  await member.dispose();

  await signIn(page, "not-admin@example.com", "not-admin-password");
  await page.goto("/admin");
  await expect(page.getByRole("tab", { name: "Users" })).toHaveCount(0);

  // Without the browser's origin, a signed-in request that changes something is refused.
  const api = await apiAs("not-admin@example.com", "not-admin-password");
  const foreign = await api.post("/api/projects", {
    data: { title: "x", topic: "y" }, headers: { Origin: "https://attacker.example" },
  });
  expect(foreign.status()).toBe(403);
  await api.dispose();
});
