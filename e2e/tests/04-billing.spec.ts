import { ADMIN, adminApi, enableEmail, expect, latestMail, newPerson, registerApi, signIn, test } from "./helpers";

const BUYER = { email: "buyer@example.com", password: "buyer-password-12" };

test("manual VietQR: the buyer reports the transfer, an administrator confirms it, the plan activates once", async ({ page, browser }) => {
  // The administrator's setup, through the API: a price for Standard, the bank account for manual VietQR.
  const admin = await adminApi();
  await enableEmail(admin);
  const plan = await admin.put("/api/admin/plans/standard", {
    data: { name: "Standard", project_limit: 20, workflow_limit: 10, monthly_credits: 1000, is_active: true, price_vnd: 199000 },
  });
  expect(plan.status(), await plan.text()).toBe(200);
  const bank = await admin.put("/api/admin/payment-config/bank_qr", {
    data: { enabled: true, bank_bin: "970407", bank_name: "", account_number: "0123456789", account_name: "REELFORGE E2E",
            transfer_prefix: "RF", note: "", sla_message: "", use_for_vietqr: true },
  });
  expect(bank.status(), await bank.text()).toBe(200);

  (await registerApi(BUYER.email, BUYER.password, "Buyer Studio")).dispose();
  await signIn(page, BUYER.email, BUYER.password);
  await page.goto("/billing");
  await expect(page.getByText("Accepted methods: VietQR / Bank Transfer")).toBeVisible();
  const standard = page.locator("#plans .panel").filter({ has: page.getByText("Standard", { exact: true }) });
  await standard.getByRole("button", { name: "Pay" }).click();

  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("VietQR bank transfer")).toBeVisible();
  await expect(dialog.getByRole("img", { name: /VietQR code for order/ })).toBeVisible();
  await expect(dialog.getByText("REELFORGE E2E")).toBeVisible();
  await dialog.getByRole("button", { name: "I have transferred" }).click();
  await expect(page.getByText("Noted. Your plan is activated when an administrator confirms the transfer.")).toBeVisible();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("You have a bank transfer waiting for an administrator to confirm it.")).toBeVisible();
  // Shown again from the payment history: already reported, nothing more to press.
  await page.getByRole("button", { name: "Show QR" }).click();
  await expect(dialog.getByRole("button", { name: "Awaiting confirmation" })).toBeDisabled();
  await dialog.getByRole("button", { name: "Later" }).click();

  // Nothing is paid yet: the plan is still Trial.
  const before = await (await admin.get("/api/admin/payments?status=awaiting_confirmation")).json();
  expect(before.total).toBe(1);
  const order = before.items[0];
  expect(order.owner_email).toBe(BUYER.email);

  const adminContext = await newPerson(browser);
  const adminPage = await adminContext.newPage();
  await signIn(adminPage, ADMIN.email, ADMIN.password);
  await adminPage.goto("/admin?tab=payments&review=1");
  await adminPage.getByRole("button", { name: `Actions · ${order.reference}` }).click();
  await adminPage.getByRole("menuitem", { name: "Confirm money received" }).click();
  const confirm = adminPage.getByRole("alertdialog");
  await expect(confirm.getByText("Confirm the money was received?")).toBeVisible();
  await confirm.getByRole("button", { name: "Confirm money received" }).click();
  await expect(confirm).toHaveCount(0);
  await adminContext.close();

  const settled = await (await admin.get(`/api/admin/payments?q=${order.reference}`)).json();
  expect(settled.items[0].status).toBe("paid");
  // A second confirmation is refused: the credits are added once.
  const again = await admin.post(`/api/admin/payments/${order.id}/confirm`, { data: { amount_vnd: order.amount_vnd } });
  expect(again.status()).toBe(409);

  await page.reload();
  await expect(page.getByText("Standard plan · Active")).toBeVisible();
  await expect(page.getByText("You have a bank transfer waiting")).toHaveCount(0);
  await page.getByRole("button", { name: "Notifications" }).click();
  await expect(page.getByText("Payment completed").first()).toBeVisible();
  const mail = await latestMail(BUYER.email, "Standard plan");
  expect(mail).toContain("199,000 VND");
});
