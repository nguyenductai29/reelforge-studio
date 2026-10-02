import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

// Browser tests of the critical, non-paid flows. Run `python e2e/prepare.py` first (it builds .stack/ and never
// touches the developer's instance/ or database); Playwright starts the SMTP sink, the API and the web server.
// No AI provider, payment gateway or social network is contacted: the flows below need none.
const stack = path.join(__dirname, ".stack");
const python = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");

export default defineConfig({
  testDir: "./tests",
  // One database: the specs run in order, in one worker.
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  // On CI, "github" turns each failure into an annotation on the run page.
  reporter: process.env.CI ? [["list"], ["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://127.0.0.1:3010",
    locale: "en-US",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: process.env.E2E_CHROMIUM_PATH ? { executablePath: process.env.E2E_CHROMIUM_PATH } : {},
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1366, height: 768 } } }],
  webServer: [
    {
      command: `${python} ../tests/smtp_sink.py --port 2526 --dir .mail`,
      port: 2526,
      reuseExistingServer: false,
    },
    {
      command: `${python} -m uvicorn app.main:app --host 127.0.0.1 --port 8010`,
      cwd: path.join(stack, "api"),
      url: "http://127.0.0.1:8010/health/live",
      env: { PYTHONPATH: path.join(stack, "api"), REELFORGE_MASTER_KEY_FILE: path.join(stack, "api", "instance", "master.key") },
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: "npx next start -p 3010 -H 127.0.0.1",
      cwd: path.join(stack, "web"),
      url: "http://127.0.0.1:3010/terms",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
