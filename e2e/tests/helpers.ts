import { createHmac } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import {
  expect, request, test as base, type APIRequestContext, type Browser, type BrowserContext, type Page,
} from "@playwright/test";

export const WEB = "http://127.0.0.1:3010";
export const ADMIN = { email: "admin@example.com", password: "admin-password-123" };
const MAIL = path.join(__dirname, "..", ".mail");

let lastOctet = 10;

/** A distinct client address for each simulated person, sent as Cloudflare sends it (CF-Connecting-IP). The API
 *  believes the header only from its trusted local proxy (here Next.js on loopback) and limits sign-ins and
 *  registrations per address, so one shared address would soon be refused, as it should be. */
export function clientIp(): string {
  lastOctet += 1;
  return `198.51.100.${lastOctet}`;  // TEST-NET-2, never routed
}

/** Every test's browser context: English, and an address of its own. */
export const test = base.extend({
  context: async ({ context }, use) => {
    await context.setExtraHTTPHeaders({ "CF-Connecting-IP": clientIp() });
    await english(context);
    await use(context);
  },
});
export { expect };

/** Another person in the same test (a second browser), with their own address. */
export async function newPerson(browser: Browser): Promise<BrowserContext> {
  const context = await browser.newContext({ extraHTTPHeaders: { "CF-Connecting-IP": clientIp() } });
  await english(context);
  return context;
}

function apiContext(): Promise<APIRequestContext> {
  return request.newContext({ baseURL: WEB, extraHTTPHeaders: { Origin: WEB, "CF-Connecting-IP": clientIp() } });
}

/** A signed-in API client (the browser's Origin, as the API requires for changes). */
export async function apiAs(email: string, password: string): Promise<APIRequestContext> {
  const context = await apiContext();
  const response = await context.post("/api/login", { data: { email, password } });
  expect(response.status(), await response.text()).toBe(200);
  return context;
}

/** A new account with its own Trial studio, signed in through the API (a second session besides any browser). */
export async function registerApi(email: string, password: string, workspace: string): Promise<APIRequestContext> {
  const context = await apiContext();
  const response = await context.post("/api/register", {
    data: { email, password, workspace_name: workspace, accept_terms: true, locale: "en" },
  });
  expect(response.status(), await response.text()).toBe(201);
  return context;
}

let admin: APIRequestContext | null = null;

/** The first administrator (created by the first spec, or here when a spec runs alone), signed in through the API.
 *  One session for the whole run: the API limits sign-ins per account and per address. */
export async function adminApi(): Promise<APIRequestContext> {
  if (admin && (await admin.get("/api/dashboard")).ok()) return admin;
  const anonymous = await apiContext();
  const status = await (await anonymous.get("/api/status")).json();
  if (status.setup_required) {
    const created = await anonymous.post("/api/setup", { data: { ...ADMIN, accept_terms: true, locale: "en" } });
    expect(created.status(), await created.text()).toBe(200);
  }
  await anonymous.dispose();
  admin = await apiAs(ADMIN.email, ADMIN.password);
  return admin;
}

/** The browser joins that one administrator session instead of signing in again. */
export async function asAdmin(page: Page) {
  const { cookies } = await (await adminApi()).storageState();
  await page.context().addCookies(cookies);
}

/** Point transactional email at the local SMTP sink (tests/smtp_sink.py on port 2526). */
export async function enableEmail(admin: APIRequestContext) {
  const response = await admin.put("/api/admin/system-config/email", {
    data: {
      values: { "email.enabled": true, "email.provider": "smtp", "email.from_email": "studio@example.com",
                "email.smtp.host": "127.0.0.1", "email.smtp.port": 2526, "email.smtp.security": "none" },
      secrets: {}, reset: [],
    },
  });
  expect(response.status(), await response.text()).toBe(200);
}

export async function english(context: BrowserContext) {
  await context.addCookies([{ name: "rf_locale", value: "en", url: WEB }]);
}

/** The sign-in form; returns once the API answered (the session cookie is set before the next navigation). */
export async function signIn(page: Page, email: string, password: string) {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  const answered = page.waitForResponse((response) =>
    response.url().endsWith("/api/login") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Sign in" }).click();
  await answered;
}

export async function signOut(page: Page) {
  await page.getByRole("button", { name: "Account", exact: true }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
}

/** RFC 6238 (SHA-1, 6 digits, 30 s), as authenticator apps compute it. */
export function totp(secret: string, offsetSteps = 0): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const char of secret.replace(/=+$/, "").toUpperCase()) bits += alphabet.indexOf(char).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g)!.map((byte) => parseInt(byte, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000) + offsetSteps));
  const digest = createHmac("sha1", key).update(counter).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const value = (digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return value.toString().padStart(6, "0");
}

function decodePart(raw: string): string {
  const [head, ...rest] = raw.split(/\r?\n\r?\n/);
  const body = rest.join("\n\n");
  if (/content-transfer-encoding:\s*base64/i.test(head)) return Buffer.from(body.replace(/\s+/g, ""), "base64").toString("utf8");
  if (/content-transfer-encoding:\s*quoted-printable/i.test(head)) {
    const bytes = body.replace(/=\r?\n/g, "").replace(/=([0-9A-F]{2})/gi, (_, hex: string) => `%${hex}`);
    try {
      return decodeURIComponent(bytes);
    } catch {
      return bytes;
    }
  }
  return body;
}

/** The newest email to ``to`` whose decoded text contains ``contains``, waiting for the sink to receive it. */
export async function latestMail(to: string, contains: string): Promise<string> {
  let found = "";
  await expect.poll(() => {
    if (!fs.existsSync(MAIL)) return false;
    const files = fs.readdirSync(MAIL).filter((name) => name.endsWith(".eml")).sort().reverse();
    for (const name of files) {
      const raw = fs.readFileSync(path.join(MAIL, name), "utf8");
      if (!new RegExp(`^To: .*${to.replace(/[.@]/g, "\\$&")}`, "mi").test(raw)) continue;
      const decoded = raw.split(/--=+[0-9a-z_=.-]+/i).map(decodePart).join("\n");
      if (!decoded.includes(contains)) continue;
      found = decoded;
      return true;
    }
    return false;
  }, { timeout: 30_000, message: `email to ${to} containing ${contains}` }).toBe(true);
  return found;
}

export function linkIn(mail: string, pathPrefix: string): string {
  const match = mail.match(new RegExp(`https?://[^\\s"'<>]+${pathPrefix}#token=[A-Za-z0-9_-]+`));
  expect(match, `a ${pathPrefix} link in the email`).not.toBeNull();
  return match![0];
}

/** Open the verification link the server emailed to ``email`` (new accounts get one when email is set up). */
export async function verifyEmail(page: Page, email: string) {
  const link = linkIn(await latestMail(email, "/verify-email#token="), "/verify-email");
  await page.goto(link);
  await expect(page.getByText(`${email} is verified.`)).toBeVisible();
}
