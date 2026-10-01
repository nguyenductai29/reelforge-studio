import { adminApi, enableEmail, expect, latestMail, registerApi, signIn, test } from "./helpers";

const USER = { email: "support-user@example.com", password: "support-password-1" };

test("support: a request from the app, the reply arrives as a notification and an email", async ({ page }) => {
  const admin = await adminApi();
  await enableEmail(admin);
  (await registerApi(USER.email, USER.password, "Support Studio")).dispose();
  await signIn(page, USER.email, USER.password);

  await page.goto("/support");
  await expect(page.getByText("No support requests yet.")).toBeVisible();
  await page.getByRole("button", { name: "New request" }).first().click();
  await page.getByLabel("Subject").fill("Export is slow");
  await page.getByLabel("Description").fill("The export of my last video takes more than ten minutes.");
  await page.getByRole("button", { name: "Send request" }).click();
  await expect(page).toHaveURL(/\/support\/[0-9a-f-]{36}$/);
  await expect(page.getByText("The export of my last video takes more than ten minutes.")).toBeVisible();

  const tickets = await (await admin.get("/api/admin/support?q=Export%20is%20slow")).json();
  expect(tickets.items).toHaveLength(1);
  const reply = await admin.post(`/api/admin/support/${tickets.items[0].id}/messages`, {
    data: { body: "Thanks, we are looking into the render queue.", status: "waiting_user" },
  });
  expect(reply.status(), await reply.text()).toBe(201);

  // The notification arrives live (server-sent events) or on the next poll.
  await expect(page.getByRole("button", { name: "Notifications" })).toContainText(/[1-9]/, { timeout: 30_000 });
  await page.getByRole("button", { name: "Notifications" }).click();
  await expect(page.getByText("New support reply").first()).toBeVisible();
  await page.reload();
  await expect(page.getByText("Thanks, we are looking into the render queue.")).toBeVisible();
  await expect(page.getByText("Waiting for you")).toBeVisible();

  await page.goto("/notifications");
  await page.getByRole("button", { name: "Mark all as read" }).click();
  await expect(page.getByRole("button", { name: "Notifications" })).not.toContainText(/[1-9]/);

  // The email points to the request; the reply itself stays in the app.
  const mail = await latestMail(USER.email, "Support replied to");
  expect(mail).toContain("Export is slow");
  expect(mail).toContain("/support/");
  expect(mail).not.toContain("render queue");
});
