#!/usr/bin/env node
/**
 * Creates the first administrator through the API's one-time setup endpoint.
 *
 * Run it right after the first release, before the site is public: until an
 * account exists, whoever opens the sign-in page first can claim the admin role.
 *
 *   npm run create-admin                                   # asks for email and password
 *   ADMIN_EMAIL=… ADMIN_PASSWORD=… npm run create-admin     # non-interactive
 *
 * The password is never accepted as a command-line argument, so it stays out of
 * shell history and process listings. Running it again after setup is a no-op.
 */
import { existsSync, readFileSync } from "node:fs";
import { createInterface } from "node:readline";

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const MIN_PASSWORD = 12;

/** Same API address the Next.js proxy uses (frontend/instance/config.json). */
function apiBase() {
  const file = new URL("../instance/config.json", import.meta.url);
  const local = existsSync(file) ? JSON.parse(readFileSync(file, "utf8")) : {};
  const base = String(local.api_base_url ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
  if (!/^https?:\/\//.test(base)) fail("api_base_url in frontend/instance/config.json must be an absolute HTTP URL.");
  return base;
}

function fail(message) {
  console.error(`✗ ${message}`);
  process.exit(1);
}

function ask(question, { hidden = false } = {}) {
  return new Promise((resolve) => {
    const rl = createInterface({ input: process.stdin, output: process.stdout, terminal: true });
    let muted = false;
    // Echo nothing while a password is typed; the prompt itself is written before muting.
    rl._writeToOutput = (text) => {
      if (!muted) rl.output.write(text);
    };
    rl.question(question, (answer) => {
      rl.close();
      if (hidden) process.stdout.write("\n");
      resolve(answer.trim());
    });
    muted = hidden;
  });
}

async function credentials() {
  let email = process.env.ADMIN_EMAIL?.trim();
  let password = process.env.ADMIN_PASSWORD;
  if (email && password) return { email, password };
  if (!process.stdin.isTTY) fail("Set ADMIN_EMAIL and ADMIN_PASSWORD, or run this in an interactive terminal.");

  email ||= await ask("Admin email: ");
  if (!EMAIL.test(email)) fail("Enter a valid email address.");
  if (!password) {
    password = await ask(`Password (at least ${MIN_PASSWORD} characters): `, { hidden: true });
    if (password.length >= MIN_PASSWORD && (await ask("Repeat password: ", { hidden: true })) !== password) {
      fail("The passwords do not match.");
    }
  }
  return { email, password };
}

async function request(base, path, init) {
  try {
    return await fetch(`${base}/api/${path}`, init);
  } catch {
    fail(`Cannot reach the API at ${base}. Start it (and apply migrations) before running this command.`);
  }
}

const base = apiBase();
const status = await request(base, "status");
if (!status.ok) fail(`The API at ${base} answered ${status.status}. Check that it is running and migrated.`);
if (!(await status.json()).setup_required) {
  console.log("✓ An administrator already exists. Nothing to do.");
  process.exit(0);
}

const { email, password } = await credentials();
if (!EMAIL.test(email)) fail("Enter a valid email address.");
if (password.length < MIN_PASSWORD) fail(`The password needs at least ${MIN_PASSWORD} characters.`);

const response = await request(base, "setup", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email, password }),
});
const body = await response.json().catch(() => ({}));
if (response.status === 409) {
  console.log("✓ An administrator already exists. Nothing to do.");
} else if (!response.ok) {
  fail(typeof body.detail === "string" ? body.detail : `Setup failed (${response.status}).`);
} else {
  console.log(`✓ Created administrator ${email.toLowerCase()} with the studio "My Studio" on the Trial plan.`);
}
