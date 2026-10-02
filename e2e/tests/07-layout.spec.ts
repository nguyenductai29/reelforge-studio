import type { Page } from "@playwright/test";
import { asAdmin, expect, test } from "./helpers";

// Settings and Admin behave like a desktop app: the window never scrolls; long regions scroll inside.
const SETTINGS = ["general", "workspace", "defaults", "members", "ai", "publishing", "storage", "security", "system"];
const ADMIN_TABS = ["users", "studios", "plans", "payments", "support", "reconciliation", "operations", "verification",
                    "system", "audit"];
// Short enough to show without any scrollbar on a laptop window or larger.
const SHORT = ["general", "workspace", "defaults", "ai", "publishing", "storage", "system"];
const PAGES = [...SETTINGS.map((tab) => `/settings?tab=${tab}`), ...ADMIN_TABS.map((tab) => `/admin?tab=${tab}`)];

async function open(page: Page, url: string) {
  await page.goto(url);
  await expect(page.getByRole("tabpanel")).toBeVisible();
  await page.waitForLoadState("networkidle");
}

/** How far the document overflows the window, each way (0 when the page itself cannot scroll), and how far the
 *  region under the top bar overflows (0 when the header and the tabs never scroll away). */
function overflowOf(page: Page) {
  return page.evaluate(() => {
    const doc = document.documentElement;
    const main = document.getElementById("main")!;
    return { down: doc.scrollHeight - doc.clientHeight, across: doc.scrollWidth - doc.clientWidth,
             main: main.scrollHeight - main.clientHeight };
  });
}

async function expectFitted(page: Page, url: string) {
  const overflow = await overflowOf(page);
  expect(overflow.down, `${url} scrolls the page`).toBeLessThanOrEqual(1);
  expect(overflow.across, `${url} scrolls the page sideways`).toBeLessThanOrEqual(1);
  expect(overflow.main, `${url} scrolls its header away`).toBeLessThanOrEqual(1);
}

for (const [width, height] of [[1366, 768], [1680, 1050]] as const) {
  test(`settings and admin never scroll the page at ${width}x${height}`, async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height });
    await asAdmin(page);
    for (const url of PAGES) {
      await open(page, url);
      await expectFitted(page, url);
    }
    // The short tabs fit their panel: no scrollbar at all.
    for (const tab of SHORT) {
      await open(page, `/settings?tab=${tab}`);
      const extra = await page.getByRole("tabpanel").evaluate((panel) => panel.scrollHeight - panel.clientHeight);
      expect(extra, `settings ${tab} needs a scrollbar`).toBeLessThanOrEqual(1);
    }
    // A long tab scrolls inside, under a header and tabs that stay put.
    await open(page, "/settings?tab=security");
    const security = page.getByRole("tabpanel");
    expect(await security.evaluate((panel) => panel.scrollHeight > panel.clientHeight)).toBe(true);
    await security.evaluate((panel) => panel.scrollTo(0, panel.scrollHeight));
    await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeInViewport();
    await expect(page.getByRole("tab", { name: "Security" })).toBeInViewport();
  });
}

for (const [device, width, height] of [["a phone", 390, 844], ["a tablet", 768, 1024]] as const) {
  test(`on ${device} (${width}x${height}) the page never scrolls, either way`, async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height });
    await asAdmin(page);
    for (const url of PAGES) {
      await open(page, url);
      await expectFitted(page, url);
    }
    // Every admin table keeps rows visible below its toolbar.
    for (const tab of ["users", "studios", "payments", "support", "reconciliation", "audit"]) {
      await open(page, `/admin?tab=${tab}`);
      const body = page.getByRole("tabpanel").locator("table").locator("..");
      expect(await body.evaluate((element) => element.clientHeight), `admin ${tab} table height`).toBeGreaterThan(120);
    }
  });
}

test("a window too short for the page scrolls the region under the top bar, and dialogs scroll on their own", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 420 });
  await asAdmin(page);
  await open(page, "/admin?tab=users");
  const overflow = await overflowOf(page);
  expect(overflow.down).toBeLessThanOrEqual(1);
  expect(overflow.across).toBeLessThanOrEqual(1);
  expect(overflow.main).toBeGreaterThan(0);

  await page.getByRole("button", { name: "Create account" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect.poll(async () => {
    const box = await dialog.boundingBox();
    return Boolean(box && box.y >= 0 && box.y + box.height <= 420 + 1);
  }).toBe(true);
  expect(await dialog.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
  await dialog.evaluate((element) => element.scrollTo(0, element.scrollHeight));
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeInViewport();
});
