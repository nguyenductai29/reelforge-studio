import type { Page } from "@playwright/test";
import {
  adminApi, apiAs, enableEmail, expect, latestMail, linkIn, newPerson, registerApi, signIn, test, verifyEmail,
} from "./helpers";

const OWNER = { email: "crew-owner@example.com", password: "crew-owner-pass-1" };
const MEMBER = { email: "crew-member@example.com", password: "crew-member-pass-1" };

/** Pick an option of a toolbar filter (the trigger keeps its "All …" name). */
async function filter(page: Page, all: string, option: string) {
  await page.getByRole("tabpanel").getByRole("combobox", { name: all }).click();
  await page.getByRole("option", { name: option, exact: true }).click();
}

test("members: search, filters and pages; invitations and member actions; nothing beyond the role", async ({ page, browser }) => {
  test.setTimeout(180_000);
  await enableEmail(await adminApi());
  const owner = await registerApi(OWNER.email, OWNER.password, "Crew Studio");
  await signIn(page, OWNER.email, OWNER.password);
  await verifyEmail(page, OWNER.email);

  // One member who joined (an existing account accepting the emailed invitation) and 21 pending invitations:
  // 23 rows, on two pages of 20.
  const member = await registerApi(MEMBER.email, MEMBER.password, "Member Home");
  expect((await owner.post("/api/workspace/invites", { data: { email: MEMBER.email, role: "editor" } })).status()).toBe(201);
  const token = linkIn(await latestMail(MEMBER.email, "/invite#token="), "/invite").split("#token=")[1];
  expect((await member.post("/api/invites/accept", { data: { token } })).status()).toBe(200);
  for (let index = 1; index <= 21; index += 1) {
    const email = `guest${String(index).padStart(2, "0")}@example.com`;
    const response = await owner.post("/api/workspace/invites", { data: { email, role: index % 3 === 0 ? "viewer" : "editor" } });
    expect(response.status(), await response.text()).toBe(201);
  }

  await page.goto("/settings?tab=members");
  const panel = page.getByRole("tabpanel");
  const search = panel.getByLabel("Search members");
  await expect(panel.getByText("1–20 of 23")).toBeVisible();
  await panel.getByRole("button", { name: "Next" }).click();
  await expect(panel.getByText("21–23 of 23")).toBeVisible();
  await panel.getByRole("button", { name: "Previous" }).click();
  await expect(panel.getByText("1–20 of 23")).toBeVisible();

  // The owner's own row: marked, and protected (no action on it).
  await expect(panel.getByRole("row", { name: new RegExp(OWNER.email) }).getByText("You")).toBeVisible();
  await expect(panel.getByRole("button", { name: `Actions · ${OWNER.email}` })).toHaveCount(0);

  // Search, then the role and status filters (server side).
  await search.fill("guest07");
  await expect(panel.getByText("1–1 of 1")).toBeVisible();
  await expect(panel.getByText("guest07@example.com")).toBeVisible();
  await search.fill("");
  await filter(page, "All roles", "Viewer");
  await expect(panel.getByText("1–7 of 7")).toBeVisible();
  await filter(page, "All roles", "All roles");
  await filter(page, "All statuses", "Active");
  await expect(panel.getByText("1–2 of 2")).toBeVisible();
  await expect(panel.getByText(MEMBER.email)).toBeVisible();
  await filter(page, "All statuses", "Pending invitation");
  await expect(panel.getByText("1–20 of 21")).toBeVisible();
  await filter(page, "All statuses", "All statuses");
  await expect(panel.getByText("1–20 of 23")).toBeVisible();

  // Invite from the dialog: the pending invitation appears among the rows.
  await panel.getByRole("button", { name: "Invite member" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Email of the person to invite").fill("newcomer@example.com");
  await dialog.getByRole("button", { name: "Send invitation" }).click();
  const sent = page.getByText("Invitation sent to newcomer@example.com.");
  await expect(sent).toBeVisible();
  await expect(dialog).toHaveCount(0);
  await search.fill("newcomer");
  await expect(panel.getByText("1–1 of 1")).toBeVisible();
  await expect(panel.getByRole("row", { name: /newcomer@example\.com/ }).getByText("Pending invitation")).toBeVisible();

  // Resend it, then cancel it.
  await expect(sent).toHaveCount(0, { timeout: 15_000 });
  await panel.getByRole("button", { name: "Actions · newcomer@example.com" }).click();
  await page.getByRole("menuitem", { name: "Resend invitation" }).click();
  await expect(sent).toBeVisible();
  await panel.getByRole("button", { name: "Actions · newcomer@example.com" }).click();
  await page.getByRole("menuitem", { name: "Cancel invitation" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  // The confirmation closes once the API answered (while it is open, the page behind it is hidden from roles).
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect(panel.getByText("newcomer@example.com")).toHaveCount(0);
  await expect(panel.getByText("0–0 of 0")).toBeVisible();

  // Change the member's role.
  await search.fill("crew-member");
  const memberRow = panel.getByRole("row", { name: new RegExp(MEMBER.email) });
  await panel.getByRole("button", { name: `Actions · ${MEMBER.email}` }).click();
  await page.getByRole("menuitem", { name: "Change role" }).click();
  await page.getByRole("menuitemradio", { name: "Viewer" }).click();
  await expect(page.getByText("Role changed.")).toBeVisible();
  await expect(memberRow.getByRole("cell", { name: "Viewer", exact: true })).toBeVisible();

  // As that viewer: the members, never the invitations, and no action at all; the API refuses anyway.
  const viewerContext = await newPerson(browser);
  const viewerPage = await viewerContext.newPage();
  await signIn(viewerPage, MEMBER.email, MEMBER.password);
  await viewerPage.goto("/settings?tab=members");
  const viewerPanel = viewerPage.getByRole("tabpanel");
  await expect(viewerPanel.getByText(OWNER.email)).toBeVisible();
  await expect(viewerPanel.getByText("1–2 of 2")).toBeVisible();
  await expect(viewerPanel.getByText("guest01@example.com")).toHaveCount(0);
  await expect(viewerPanel.getByRole("button", { name: "Invite member" })).toHaveCount(0);
  await expect(viewerPanel.getByRole("button", { name: /^Actions · / })).toHaveCount(0);
  await expect(viewerPanel.getByRole("combobox", { name: "All statuses" })).toHaveCount(0);
  const viewerApi = await apiAs(MEMBER.email, MEMBER.password);
  const rows = (await (await owner.get("/api/workspace/members/page")).json()).items;
  const ownerId = rows.find((row: { you?: boolean }) => row.you).id;
  expect((await viewerApi.post("/api/workspace/invites", { data: { email: "x@example.com", role: "editor" } })).status()).toBe(403);
  expect((await viewerApi.put(`/api/workspace/members/${ownerId}`, { data: { role: "viewer" } })).status()).toBe(403);
  expect((await viewerApi.delete(`/api/workspace/members/${ownerId}`)).status()).toBe(403);
  // Nobody removes the owner, not even the owner.
  expect((await owner.delete(`/api/workspace/members/${ownerId}`)).status()).toBe(409);

  // Remove the member: access ends at once.
  await panel.getByRole("button", { name: `Actions · ${MEMBER.email}` }).click();
  await page.getByRole("menuitem", { name: "Remove member" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect(panel.getByText(MEMBER.email)).toHaveCount(0);
  const after = await (await viewerApi.get("/api/dashboard")).json();
  expect(after.workspace.name).toBe("Member Home");

  await viewerApi.dispose();
  await viewerContext.close();
  await member.dispose();
  await owner.dispose();
});
