import type { Page } from "@playwright/test";
import {
  adminApi, enableEmail, expect, latestMail, linkIn, newPerson, registerApi, signIn, test, verifyEmail,
} from "./helpers";

const OWNER = { email: "home-owner@example.com", password: "home-owner-pass-1" };
const VIEWER = { email: "home-viewer@example.com", password: "home-viewer-pass-1" };
// An input-only workflow: its run completes at once, without any AI provider or credit.
const IDEA_ONLY = { nodes: [{ id: "idea", type: "idea", x: 0, y: 0 }], edges: [] };
const PROJECT = "YouTube Short: A teaser for the autumn launch";

const region = (page: Page, name: string | RegExp) => page.getByRole("region", { name, exact: typeof name === "string" });

test("home: overview, what needs attention, quick create and recent work; a viewer only reads", async ({ page, browser }) => {
  test.setTimeout(180_000);
  const admin = await adminApi();
  await enableEmail(admin);
  const owner = await registerApi(OWNER.email, OWNER.password, "Home Studio");
  await signIn(page, OWNER.email, OWNER.password);

  // A new studio: every number at zero, one thing to handle (no credits), an empty state per section.
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "What do you want to create today?" })).toBeVisible();
  const overview = region(page, "Overview");
  for (const [name, href] of [[/^Credits 0/, "/billing"], [/^Projects 0/, "/projects"], [/^Runs · 30 days 0/, "/workflows"],
                              [/^Published · 30 days 0/, "/publishing"], [/^Storage 0 B/, "/settings?tab=storage"]] as const) {
    await expect(overview.getByRole("link", { name })).toHaveAttribute("href", href);
  }
  const attention = region(page, "Needs attention");
  await expect(attention.getByRole("link")).toHaveCount(1);
  await expect(attention.getByRole("link", { name: /Credits running low: 0 left/ })).toHaveAttribute("href", "/billing");
  for (const text of ["No workflows yet", "Create your first project", "No AI usage yet",
                      "Connect a channel and publish your first video", "No runs yet"]) {
    await expect(page.getByText(text, { exact: true })).toBeVisible();
  }
  await expect(region(page, "Recent workflows").getByRole("link", { name: "Start from a template" })).toHaveAttribute("href", "/create");
  await expect(region(page, "Publishing").getByRole("link", { name: "Connect a channel" })).toHaveAttribute("href", "/channels");

  // Quick Create: six templates and the full list one click away; a shortcut opens its new workflow.
  const quick = region(page, "Quick Create");
  await expect(quick.getByRole("button")).toHaveCount(6);
  await expect(quick.getByRole("link", { name: "View all templates" })).toHaveAttribute("href", "/create");
  await quick.getByRole("button", { name: /^TikTok Video/ }).click();
  await expect(page).toHaveURL(/\/workflows\/[0-9a-f-]{36}$/);

  // Create with AI: a project from the idea, in the chosen format.
  await page.goto("/");
  await page.getByRole("textbox", { name: "What do you want to create today?" }).fill("A teaser for the autumn launch");
  await page.getByRole("group", { name: "Content type" }).getByRole("button", { name: "YouTube Short" }).click();
  await page.getByRole("button", { name: "Create with AI" }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  const projectId = page.url().split("/projects/")[1];

  // A finished run, and a support reply waiting for the owner.
  const flow = await (await owner.post("/api/workflows", { data: { name: "Idea board" } })).json();
  expect((await owner.put(`/api/workflows/${flow.id}`, { data: IDEA_ONLY })).status()).toBe(200);
  const started = await owner.post(`/api/workflows/${flow.id}/runs`, { data: { project_id: projectId } });
  expect(started.status(), await started.text()).toBe(201);
  const runId = (await started.json()).id;
  const ticket = await owner.post("/api/support/tickets", {
    data: { subject: "Where is my export?", category: "other", description: "The export did not arrive." } });
  expect(ticket.status(), await ticket.text()).toBe(201);
  const ticketId = (await ticket.json()).id;
  const reply = await admin.post(`/api/admin/support/${ticketId}/messages`, { data: { body: "It is ready now.", status: "waiting_user" } });
  expect(reply.status(), await reply.text()).toBe(201);

  await page.goto("/");
  await expect(overview.getByRole("link", { name: /^Projects 1/ })).toBeVisible();
  await expect(overview.getByRole("link", { name: /^Runs · 30 days 1/ })).toBeVisible();
  const workflows = region(page, "Recent workflows");
  await expect(workflows.getByRole("link", { name: /Idea board.*Completed/ })).toHaveAttribute("href", `/workflows/${flow.id}`);
  await expect(workflows.getByRole("link", { name: /TikTok Video.*Not run/ })).toBeVisible();
  await expect(region(page, "Recent projects").getByRole("link", { name: new RegExp(`${PROJECT}.*Draft`) }))
    .toHaveAttribute("href", `/projects/${projectId}`);
  const runs = region(page, "Recent runs");
  await expect(runs.getByRole("row", { name: new RegExp(`${PROJECT}.*Idea board.*Completed`) })).toBeVisible();
  await expect(runs.getByRole("link", { name: PROJECT })).toHaveAttribute("href", `/workflows/${flow.id}?run=${runId}`);
  await expect(region(page, /^AI usage/).getByText("No AI usage yet")).toBeVisible();

  // Each item of "Needs attention" opens what needs the owner.
  await attention.getByRole("link", { name: /Support replied to your request/ }).click();
  await expect(page).toHaveURL(new RegExp(`/support/${ticketId}$`));
  await page.goto("/");
  await attention.getByRole("link", { name: /Credits running low/ }).click();
  await expect(page).toHaveURL(/\/billing$/);

  // No sideways scrolling on a phone, a tablet or a large window.
  for (const [width, height] of [[390, 844], [768, 1024], [1680, 1050]] as const) {
    await page.setViewportSize({ width, height });
    await page.goto("/");
    await expect(region(page, "Recent runs").getByRole("row", { name: /Idea board/ })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth),
           `${width}x${height}`).toBeLessThanOrEqual(1);
  }

  // A viewer follows the studio but creates nothing, and has nothing to handle.
  await verifyEmail(page, OWNER.email);
  const invited = await owner.post("/api/workspace/invites", { data: { email: VIEWER.email, role: "viewer" } });
  expect(invited.status(), await invited.text()).toBe(201);
  const viewerApi = await registerApi(VIEWER.email, VIEWER.password, "Viewer Own Studio");
  const token = linkIn(await latestMail(VIEWER.email, "/invite#token="), "/invite").split("#token=")[1];
  expect((await viewerApi.post("/api/invites/accept", { data: { token } })).status()).toBe(200);
  const viewerContext = await newPerson(browser);
  const viewerPage = await viewerContext.newPage();
  await signIn(viewerPage, VIEWER.email, VIEWER.password);
  await viewerPage.goto("/");
  await expect(viewerPage.getByRole("heading", { level: 1, name: "Home Studio" })).toBeVisible();
  await expect(viewerPage.getByText(/You can follow this studio's projects, runs and publishing/)).toBeVisible();
  await expect(viewerPage.getByRole("textbox", { name: "What do you want to create today?" })).toHaveCount(0);
  await expect(viewerPage.getByRole("button", { name: "Create with AI" })).toHaveCount(0);
  await expect(region(viewerPage, "Quick Create")).toHaveCount(0);
  await expect(viewerPage.getByText("Get started with ReelForge")).toHaveCount(0);
  await expect(region(viewerPage, "Needs attention").getByText("No action needed")).toBeVisible();
  await expect(region(viewerPage, "Overview").getByRole("link", { name: /^Projects 1/ })).toBeVisible();
  await expect(region(viewerPage, "Recent runs").getByRole("row", { name: /Idea board/ })).toBeVisible();
  await expect(region(viewerPage, "Publishing").getByRole("link", { name: "Connect a channel" })).toHaveCount(0);
  const summary = await (await viewerApi.get("/api/home")).json();
  expect(summary.attention).toEqual([]);

  await viewerApi.dispose();
  await viewerContext.close();
  await owner.dispose();
});
