import type { Page } from "@playwright/test";
import { asAdmin, expect, test } from "./helpers";

// The app uses the width beside the sidebar: modest gutters (24 px on a desktop), nothing ever wider than the window.
const PAGES = ["/", "/create", "/projects", "/workflows", "/library", "/publishing", "/calendar", "/models", "/media",
               "/channels", "/billing", "/settings", "/admin", "/notifications", "/support"];
const DESKTOP_GUTTER = 24;

async function open(page: Page, url: string) {
  await page.goto(url);
  await expect(page.locator("#main")).toBeVisible();
  await page.waitForLoadState("networkidle");
}

/** How far the document is wider than the window, and how the main area's content box compares with the room. */
function measure(page: Page) {
  return page.evaluate(() => {
    const doc = document.documentElement;
    const main = document.getElementById("main")!;
    const style = getComputedStyle(main);
    const width = main.getBoundingClientRect().width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    const sidebar = document.querySelector("aside")?.getBoundingClientRect().width ?? 0;
    return { sideways: doc.scrollWidth - doc.clientWidth, content: width, room: window.innerWidth - sidebar };
  });
}

/** Steps of the workflow mini diagrams (28 px squares in a box that hides what overflows) that their box cuts off. */
function clippedSteps(page: Page) {
  return page.evaluate(() => [...document.querySelectorAll("#main *")].flatMap((step) => {
    const rect = step.getBoundingClientRect();
    const box = step.parentElement!;
    if (Math.round(rect.width) !== 28 || Math.round(rect.height) !== 28 || getComputedStyle(box).overflowX !== "hidden") return [];
    const outer = box.getBoundingClientRect();
    return rect.left < outer.left - 0.5 || rect.right > outer.right + 0.5
      ? [`${Math.round(rect.left)}–${Math.round(rect.right)} in ${Math.round(outer.left)}–${Math.round(outer.right)}`] : [];
  }));
}

for (const [width, height] of [[390, 844], [768, 1024], [1024, 768], [1366, 768], [1440, 900], [1680, 1050],
                               [1920, 1080]] as const) {
  test(`every main page fits ${width}x${height} without sideways scrolling${width >= 1366 ? ", using the width beside the sidebar" : ""}`, async ({ page }) => {
    test.setTimeout(180_000);
    await page.setViewportSize({ width, height });
    await asAdmin(page);
    for (const url of PAGES) {
      await open(page, url);
      const { sideways, content, room } = await measure(page);
      expect(sideways, `${url} scrolls sideways`).toBeLessThanOrEqual(1);
      // A narrow card drops the last steps of its diagram rather than cutting off the first and the last.
      if (url === "/create" || url === "/workflows") expect(await clippedSteps(page), `${url} diagrams`).toEqual([]);
      if (width >= 1366) {
        // Only the two gutters are left unused, whatever the window: no centred column with empty margins.
        expect(content, `${url} content width`).toBeGreaterThanOrEqual(room - 2 * DESKTOP_GUTTER - 2);
      }
    }
    if (width >= 1366) {
      // Admin → Plans: the first three plans side by side.
      await open(page, "/admin?tab=plans");
      const plans = await page.getByRole("tabpanel").locator("form").evaluateAll((forms) =>
        forms.map((form) => Math.round(form.getBoundingClientRect().top)));
      expect(plans.length).toBeGreaterThanOrEqual(3);
      expect(new Set(plans.slice(0, 3)).size, `plan rows ${plans}`).toBe(1);
    }
  });
}

for (const [width, height, columns] of [[1680, 1050, 4], [1920, 1080, 6]] as const) {
  test(`at ${width}x${height}: admin figures in one row and panels side by side, members across, ${columns} templates a row`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await asAdmin(page);
    await open(page, "/admin");
    const overview = page.getByRole("tabpanel");
    const tiles = overview.getByRole("region", { name: "Overview" }).getByRole("link");
    await expect(tiles).toHaveCount(7);
    const tops = await tiles.evaluateAll((links) => links.map((link) => Math.round(link.getBoundingClientRect().top)));
    expect(new Set(tops).size, `KPI rows ${tops}`).toBe(1);
    const panels = await Promise.all(["System health", /^AI usage/, "Payments"].map((name) =>
      overview.getByRole("region", { name }).evaluate((section) => Math.round(section.getBoundingClientRect().top))));
    expect(new Set(panels).size, `panel rows ${panels}`).toBe(1);

    // Settings → Members: the table takes the whole width of the page.
    await open(page, "/settings?tab=members");
    const table = await page.getByRole("tabpanel").locator("table").evaluate((element) => element.getBoundingClientRect().width);
    const { content } = await measure(page);
    expect(table).toBeGreaterThan(content - 40);

    // Card grids add a column from 1800 px (the 3xl breakpoint): the eleven templates four, then six, to a row.
    await open(page, "/create");
    // The template cards only: a banner above them (an unverified email, say) has buttons of its own.
    const cards = page.locator("#main").getByRole("button", { name: /(\d+ steps|Empty canvas)$/ });
    await expect(cards).toHaveCount(11);
    const rows = await cards.evaluateAll((buttons) => buttons.map((button) => Math.round(button.getBoundingClientRect().top)));
    expect(rows.filter((top) => top === rows[0]).length, `template rows ${rows}`).toBe(columns);
  });
}
