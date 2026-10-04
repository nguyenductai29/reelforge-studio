import {
  ADMIN, adminApi, apiAs, enableEmail, english, expect, latestMail, linkIn, registerApi, signIn, signOut, test, totp,
} from "./helpers";

test("the first administrator creates the studio, signs out and signs in again", async ({ page, browser }) => {
  // A visitor from the internet (a public address, as Cloudflare reports it) cannot claim the first administrator.
  const visitor = await browser.newContext({ extraHTTPHeaders: { "CF-Connecting-IP": "93.184.216.34" } });
  await english(visitor);
  const outsider = await visitor.newPage();
  await outsider.goto("/");
  await expect(outsider.getByText(/created on the server itself/)).toBeVisible();
  await expect(outsider.getByText("cd frontend && npm run create-admin")).toBeVisible();
  await expect(outsider.getByRole("button", { name: "Create studio" })).toHaveCount(0);
  await visitor.close();

  // On the server's side of the proxy (here a TEST-NET address, never a public one), the form works.
  await page.goto("/");
  await expect(page.getByText("Create your studio")).toBeVisible();
  await page.getByLabel("Email").fill(ADMIN.email);
  await page.getByLabel("Password", { exact: true }).fill(ADMIN.password);
  await page.getByRole("checkbox", { name: /I agree to the/ }).check();
  await page.getByRole("button", { name: "Create studio" }).click();
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();
  await signOut(page);

  // A wrong password says nothing about the account; the right one signs in.
  await signIn(page, ADMIN.email, "not-the-password-1");
  await expect(page.getByText("The email or password is not correct.")).toBeVisible();
  await signIn(page, ADMIN.email, ADMIN.password);
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();
});

test("forgot password: a generic answer, an emailed link, a new password, every old session ended", async ({ page }) => {
  await enableEmail(await adminApi());
  const email = "reset-user@example.com";
  const other = await registerApi(email, "first-password-123", "Reset Studio");

  // An unknown address gets the same answer as a real one.
  await page.goto("/forgot-password");
  await page.getByLabel("Email").fill("nobody@example.com");
  await page.getByRole("button", { name: "Send link" }).click();
  const generic = page.getByText("If this email has an account, a reset link is on its way.");
  await expect(generic).toBeVisible();

  await page.goto("/forgot-password");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Send link" }).click();
  await expect(generic).toBeVisible();
  const link = linkIn(await latestMail(email, "/reset-password#token="), "/reset-password");

  await page.goto(link);
  await page.getByLabel("New password").fill("second-password-456");
  await page.getByLabel("Repeat the password").fill("second-password-456");
  await page.getByRole("button", { name: "Set password" }).click();
  await expect(page.getByText("Your new password is set.")).toBeVisible();
  expect((await other.get("/api/dashboard")).status()).toBe(401);

  // The link works once (another page first: the same address with only a new #fragment would not reload).
  await page.goto("/terms");
  await page.goto(link);
  await expect(page.getByText("This link is invalid or has expired.")).toBeVisible();

  await signIn(page, email, "second-password-456");
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();
  await other.dispose();
});

test("sessions: the list shows this device, and the other sessions can be signed out", async ({ page }) => {
  const email = "sessions-user@example.com";
  const other = await registerApi(email, "sessions-password-1", "Sessions Studio");
  await signIn(page, email, "sessions-password-1");
  await page.goto("/settings?tab=security");
  await expect(page.getByText("This session")).toBeVisible();
  await page.getByRole("button", { name: "Sign out all other sessions" }).click();
  await expect(page.getByText("No other sessions.")).toBeVisible();
  expect((await other.get("/api/dashboard")).status()).toBe(401);
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();
  await other.dispose();

  // Signed out from another device: the next request of this tab brings back the sign-in screen.
  const elsewhere = await apiAs(email, "sessions-password-1");
  expect((await elsewhere.post("/api/account/sessions/revoke-others")).status()).toBe(200);
  await page.getByRole("navigation", { name: "Navigation" }).getByRole("link", { name: "Library" }).click();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await elsewhere.dispose();
});

test("2FA: enrolment needs a correct code; sign-in asks for it; a recovery code works once", async ({ page }) => {
  const email = "twofa-user@example.com";
  const password = "twofa-password-12";
  (await registerApi(email, password, "2FA Studio")).dispose();
  await signIn(page, email, password);
  await page.goto("/settings?tab=security");

  await page.getByRole("button", { name: "Turn on 2FA" }).click();
  await page.getByLabel("Enter your password to continue").fill(password);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("img", { name: /QR code to add ReelForge Studio/ })).toBeVisible();
  const secret = (await page.locator("code").filter({ hasText: /^[A-Z2-7]{16,}$/ }).first().textContent())!.trim();

  await page.getByLabel("6-digit code").fill(totp(secret, 5));  // outside the accepted window
  await page.getByRole("button", { name: "Confirm and turn on" }).click();
  await expect(page.getByText("The verification code is not correct.")).toBeVisible();
  await page.getByLabel("6-digit code").fill(totp(secret));
  await page.getByRole("button", { name: "Confirm and turn on" }).click();
  const recovery = page.getByRole("list", { name: "Recovery codes" }).getByRole("listitem");
  await expect(recovery).toHaveCount(10);
  const codes = (await recovery.allTextContents()).map((code) => code.trim());
  await page.getByRole("button", { name: "I saved the codes" }).click();
  await expect(page.getByText("10 recovery codes left")).toBeVisible();

  // The password alone no longer signs in, through the browser or the API.
  await signOut(page);
  await signIn(page, email, password);
  await expect(page.getByText("Two-factor authentication")).toBeVisible();
  await page.getByLabel("Verification code").fill("123456");
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByText("The verification code is not correct.")).toBeVisible();
  // The enrolment code was this step's; the next step's code is new (codes are never accepted twice).
  await page.getByLabel("Verification code").fill(totp(secret, 1));
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();

  await signOut(page);
  await signIn(page, email, password);
  await page.getByLabel("Verification code").fill(codes[0]!);
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();
  await signOut(page);
  await signIn(page, email, password);
  await page.getByLabel("Verification code").fill(codes[0]!);
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByText("The verification code is not correct.")).toBeVisible();

  const api = await apiAs(email, password);
  expect((await api.get("/api/dashboard")).status()).toBe(401);
  await api.dispose();
});
