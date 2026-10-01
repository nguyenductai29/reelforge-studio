import { expect, registerApi, signIn, test } from "./helpers";

test("a project and a workflow are created from the studio pages", async ({ page }) => {
  (await registerApi("studio-user@example.com", "studio-password-1", "Creator Studio")).dispose();
  await signIn(page, "studio-user@example.com", "studio-password-1");

  // First steps, from the studio's own data: nothing done yet.
  await expect(page.getByText("Get started with ReelForge")).toBeVisible();
  await expect(page.getByText("0 of 6 steps")).toBeVisible();

  await page.goto("/projects");
  await page.getByRole("button", { name: "New project" }).first().click();
  await page.getByLabel("Project name").fill("Launch video");
  await page.getByLabel("Topic").fill("A short teaser for the product launch");
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("heading", { name: "Launch video" })).toBeVisible();

  await page.goto("/workflows");
  await page.getByRole("button", { name: "Create Workflow" }).first().click();
  await expect(page).toHaveURL(/\/workflows\/[0-9a-f-]{36}$/);
  await page.goto("/workflows");
  await expect(page.getByText("No workflows yet.")).toHaveCount(0);

  await page.goto("/");
  await expect(page.getByText("2 of 6 steps")).toBeVisible();
});
