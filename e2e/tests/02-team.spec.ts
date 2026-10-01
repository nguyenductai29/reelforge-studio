import type { Page } from "@playwright/test";
import {
  adminApi, apiAs, enableEmail, expect, latestMail, linkIn, newPerson, registerApi, signIn, test, verifyEmail,
} from "./helpers";

const OWNER = { email: "owner@example.com", password: "owner-password-12" };

async function switchTo(page: Page, studio: RegExp) {
  await page.getByRole("button", { name: "Studio" }).click();
  await page.getByRole("menuitem", { name: studio }).click();
  await expect(page.getByText(/^Switched to /)).toBeVisible();
}

test("invitations: a new person creates an account and joins; an existing account signs in and joins", async ({ page, browser }) => {
  await enableEmail(await adminApi());
  const owner = await registerApi(OWNER.email, OWNER.password, "Owner Studio");
  expect((await owner.post("/api/projects", { data: { title: "Owner project", topic: "kept in Owner Studio" } })).status()).toBe(201);
  const existing = await registerApi("viewer@example.com", "viewer-password-1", "Viewer Studio");
  expect((await existing.post("/api/projects", { data: { title: "Viewer project", topic: "own" } })).status()).toBe(201);

  await signIn(page, OWNER.email, OWNER.password);
  // Inviting needs a verified address (when the server sends email): the link from the welcome email.
  await page.goto("/settings?tab=members");
  await expect(page.getByText("Verify your email address before inviting members.")).toBeVisible();
  await verifyEmail(page, OWNER.email);
  await page.goto("/settings?tab=members");
  await page.getByLabel("Email of the person to invite").fill("editor@example.com");
  await page.getByRole("button", { name: "Send invitation" }).click();
  await expect(page.getByText("Invitation sent to editor@example.com.")).toBeVisible();

  // A new person: the emailed link, a password, the terms; they land in the owner's studio as an editor.
  const editorLink = linkIn(await latestMail("editor@example.com", "/invite#token="), "/invite");
  const editorContext = await newPerson(browser);
  const editor = await editorContext.newPage();
  await editor.goto(editorLink);
  await expect(editor.getByText('You are invited to "Owner Studio" as Editor.')).toBeVisible();
  await editor.getByLabel("Password", { exact: true }).fill("editor-password-1");
  await editor.getByRole("checkbox", { name: /I agree to the/ }).check();
  await editor.getByRole("button", { name: "Create an account to join" }).click();
  await expect(editor.getByRole("button", { name: "Studio" })).toContainText("Owner Studio");
  await editor.goto("/projects");
  await expect(editor.getByText("Owner project")).toBeVisible();

  // The same link does not work twice.
  await editor.goto(editorLink);
  await expect(editor.getByText("This invitation was already used.")).toBeVisible();

  // The editor's own studio: created from the switcher, separate from the owner's.
  await editor.goto("/");
  await editor.getByRole("button", { name: "Studio" }).click();
  await editor.getByRole("menuitem", { name: "Create my own studio" }).click();
  await editor.getByLabel("Studio name").fill("Editor Studio");
  await editor.getByRole("button", { name: "Create my own studio" }).click();
  await expect(editor.getByRole("button", { name: "Studio" })).toContainText("Editor Studio");
  await editor.goto("/projects");
  await expect(editor.getByText("Owner project")).toHaveCount(0);
  await switchTo(editor, /Owner Studio/);
  await editor.goto("/projects");
  await expect(editor.getByText("Owner project")).toBeVisible();
  await editorContext.close();

  // An existing account, invited as a viewer: signs in on the invitation page and joins.
  await page.goto("/settings?tab=members");
  await page.getByLabel("Email of the person to invite").fill("viewer@example.com");
  await page.locator("#invite-role").click();
  await page.getByRole("option", { name: "Viewer" }).click();
  await page.getByRole("button", { name: "Send invitation" }).click();
  await expect(page.getByText("Invitation sent to viewer@example.com.")).toBeVisible();
  const viewerLink = linkIn(await latestMail("viewer@example.com", "/invite#token="), "/invite");
  const viewerContext = await newPerson(browser);
  const viewer = await viewerContext.newPage();
  await viewer.goto(viewerLink);
  await viewer.getByLabel("Password", { exact: true }).fill("viewer-password-1");
  await viewer.getByRole("button", { name: "Sign in to accept" }).click();
  await expect(viewer.getByRole("button", { name: "Studio" })).toContainText("Owner Studio");
  await viewer.goto("/projects");
  await expect(viewer.getByText("Owner project")).toBeVisible();
  await expect(viewer.getByText("Viewer project")).toHaveCount(0);
  await expect(viewer.getByRole("button", { name: "New project" })).toHaveCount(0);

  // The server refuses what the role does not allow, whatever the page shows.
  const viewerApi = await apiAs("viewer@example.com", "viewer-password-1");
  const switched = await viewerApi.get("/api/workspaces");
  const ownerStudio = (await switched.json()).items.find((item: { name: string }) => item.name === "Owner Studio");
  expect((await viewerApi.post(`/api/workspaces/${ownerStudio.id}/switch`)).status()).toBe(200);
  expect((await viewerApi.post("/api/projects", { data: { title: "Not allowed", topic: "x" } })).status()).toBe(403);
  expect((await viewerApi.post("/api/workspace/invites", { data: { email: "x@example.com", role: "admin" } })).status()).toBe(403);

  // Removed from the studio: refused at once; their own studio stays theirs.
  await page.goto("/settings?tab=members");
  await page.getByRole("button", { name: "Remove from studio: viewer@example.com" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("viewer@example.com")).toHaveCount(0);
  const dashboard = await (await viewerApi.get("/api/dashboard")).json();
  expect(dashboard.workspace.name).toBe("Viewer Studio");
  expect(dashboard.projects.map((project: { title: string }) => project.title)).toEqual(["Viewer project"]);
  await viewerApi.dispose();
  await viewerContext.close();
  await owner.dispose();
  await existing.dispose();
});
