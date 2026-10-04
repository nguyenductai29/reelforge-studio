import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { asAdmin, adminApi, english, expect, registerApi, signIn, test } from "./helpers";

// Movie sources end to end against the fake Google Drive (tests/fake_drive.py on :8021). The movie workers run in
// e2e/movie_driver.py with every provider and FFmpeg faked; direct URLs are served by its in-process mock.
const STACK = path.join(__dirname, "..", ".stack");
const API_DIR = path.join(STACK, "api");
const IMPORT_ROOT = path.join(STACK, "import");
const DRIVE = "http://127.0.0.1:8021";
const PYTHON = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");
const OWNER = { email: "movie-owner@example.com", password: "movie-password-123" };

/** One pass of the movie worker lanes (``sources``) or a whole review (``pipeline <run id>``). */
function driver(...args: string[]): string {
  return execFileSync(PYTHON, [path.join(__dirname, "..", "movie_driver.py"), ...args], {
    cwd: API_DIR,
    env: {
      ...process.env,
      PYTHONPATH: API_DIR,
      PYTHONIOENCODING: "utf-8",
      REELFORGE_MASTER_KEY_FILE: path.join(API_DIR, "instance", "master.key"),
      REELFORGE_GOOGLE_API_BASE: DRIVE,
    },
    encoding: "utf-8",
    timeout: 120_000,
  });
}

test("movie sources: server file, URL and Drive imports; a review; retention; deletion refused while in use", async ({ page }) => {
  test.setTimeout(300_000);
  const admin = await adminApi();
  // The system administrator turns movie sources on with the (fake) Drive, and gives the AI providers placeholder
  // keys: no provider is ever called, the driver answers for them.
  const saved = await admin.put("/api/admin/system-config/movie_sources", {
    data: {
      values: { "movie_sources.enabled": true, "movie_sources.local_import_root": IMPORT_ROOT,
                "movie_sources.drive.enabled": true, "movie_sources.drive.root_folder_id": "rootfolder01",
                "movie_sources.drive.client_id": "e2e-client.apps.googleusercontent.com" },
      secrets: { "movie_sources.drive.client_secret": { action: "replace", value: "e2e-drive-secret" },
                 "movie_sources.drive.refresh_token": { action: "replace", value: "e2e-drive-refresh" } },
      reset: [],
    },
  });
  expect(saved.status(), await saved.text()).toBe(200);
  expect(await saved.text()).not.toContain("e2e-drive-refresh");
  for (const provider of ["gemini", "openai"]) {
    const ai = await admin.put("/api/admin/system-config/ai", {
      data: { values: { [`ai.${provider}.enabled`]: true },
              secrets: { [`ai.${provider}.api_key`]: { action: "replace", value: "e2e-not-a-real-key" } }, reset: [] },
    });
    expect(ai.status(), await ai.text()).toBe(200);
  }

  // Admin → System settings → Movie sources: the Drive connection test passes and leaves nothing behind.
  await asAdmin(page);
  await page.goto("/admin?tab=system&section=movie_sources");
  await page.getByRole("button", { name: "Movie sources", exact: true }).click();
  await page.getByRole("button", { name: "Test Drive connection" }).click();
  const results = page.getByRole("list", { name: "Test Drive connection" });
  await expect(results.getByText(/Root folder: OK/)).toBeVisible();
  await expect(results.getByText(/Test file deleted: OK/)).toBeVisible();
  const state = await (await fetch(`${DRIVE}/__test__/state`)).json();
  expect(state.files.filter((file: { mimeType: string; trashed: boolean }) =>
    file.mimeType !== "application/vnd.google-apps.folder" && !file.trashed)).toEqual([]);
  await page.context().clearCookies();
  await english(page.context());

  // A studio owner with credits, a project and the three AI models the review needs.
  const owner = await registerApi(OWNER.email, OWNER.password, "Movie Studio");
  const workspace = (await (await owner.get("/api/dashboard")).json()).workspace.id;
  // The operator places a movie in this studio's import folder, <import root>/<workspace id>/ (one per studio).
  fs.mkdirSync(path.join(IMPORT_ROOT, workspace, "films"), { recursive: true });
  fs.writeFileSync(path.join(IMPORT_ROOT, workspace, "films", "night-forest.mp4"),
                   Buffer.concat([Buffer.from("00000018", "hex"), Buffer.from("ftypmp42", "latin1"), Buffer.alloc(4),
                                  Buffer.from("mp42isom", "latin1"), Buffer.alloc(300_000, 7)]));
  expect((await admin.post(`/api/admin/workspaces/${workspace}/credits`, { data: { delta: 500, reason: "e2e movie review" } })).status()).toBe(200);
  const project = await (await owner.post("/api/projects", { data: { title: "Movie night", topic: "A short review" } })).json();
  for (const [task, provider, model] of [["script", "gemini", "gemini-2.5-flash"], ["transcription", "openai", "whisper-1"],
                                         ["voice", "gemini", "gemini-2.5-flash-preview-tts"]]) {
    const tool = await owner.post("/api/ai-tools", { data: { task, provider, model } });
    expect(tool.status(), await tool.text()).toBe(201);
  }

  await signIn(page, OWNER.email, OWNER.password);
  await page.goto("/media");
  await page.getByRole("link", { name: "Movie sources" }).click();
  await expect(page).toHaveURL(/\/media\/movie-sources$/);
  await expect(page.getByRole("heading", { name: "Movie sources", level: 1 })).toBeVisible();
  await expect(page.getByText(/Use only movies you are authorized to use/).first()).toBeVisible();

  async function add(tab: string, choose: () => Promise<void>) {
    await page.getByRole("button", { name: "Add movie source" }).click();
    const dialog = page.getByRole("dialog", { name: "Add a movie source" });
    await dialog.getByRole("tab", { name: tab }).click();
    await choose();
    await dialog.getByRole("combobox", { name: "Project (optional)" }).click();
    await page.getByRole("option", { name: "Movie night" }).click();
    await dialog.getByRole("checkbox", { name: "I confirm I am authorized to use this movie." }).check();
    await dialog.getByRole("button", { name: "Add and import" }).click();
    await expect(page.getByText("The import is queued. You will be notified when the movie is ready.")).toBeVisible();
    await expect(dialog).toBeHidden();
  }

  const row = (name: string | RegExp) => page.getByRole("row").filter({ hasText: name });

  // 1. A file from the server's import folder, browsed by folder.
  await add("Server file", async () => {
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: /^films/ }).click();
    await dialog.getByRole("button", { name: /night-forest\.mp4/ }).click();
  });
  await expect(row("night-forest.mp4").getByText("Importing")).toBeVisible();
  driver("sources");
  await expect(row("night-forest.mp4").getByText("Ready")).toBeVisible({ timeout: 20_000 });
  await expect(row("night-forest.mp4").getByRole("cell", { name: "Movie night" })).toBeVisible();

  // 2. A direct https URL (served by the driver's mock behind a public address) and one that redirects inside.
  await add("Direct URL", async () => {
    await page.getByRole("dialog").getByLabel("https:// address of the movie file").fill("https://media.example.com/films/trailer.mp4");
  });
  await add("Direct URL", async () => {
    await page.getByRole("dialog").getByLabel("https:// address of the movie file").fill("https://media.example.com/redirect.mp4");
  });
  driver("sources");
  await expect(row("trailer.mp4").getByText("Ready")).toBeVisible({ timeout: 20_000 });
  await expect(row("redirect.mp4").getByText("Failed")).toBeVisible();
  await expect(row("redirect.mp4").getByText("Only public https:// addresses; internal addresses are blocked.")).toBeVisible();

  // 3. A movie the operator placed in the studio's Drive inbox.
  const placed = await fetch(`${DRIVE}/__test__/inbox`, {
    method: "POST",
    body: JSON.stringify({ workspace_id: workspace, name: "From Drive.mp4", hex: Buffer.from("\u0000\u0000\u0000\u0018ftypmp42\u0000\u0000\u0000\u0000mp42isom" + "x".repeat(4000)).toString("hex") }),
  });
  expect(placed.status).toBe(200);
  await add("Google Drive", async () => {
    await page.getByRole("dialog").getByRole("button", { name: /From Drive\.mp4/ }).click();
  });
  driver("sources");
  await expect(row("From Drive.mp4").getByText("Ready")).toBeVisible({ timeout: 20_000 });

  // Details: what ffprobe found, never a path outside the import folder or a Drive ID.
  await row("night-forest.mp4").getByRole("button", { name: /^night-forest\.mp4/ }).click();
  const detail = page.getByRole("dialog", { name: "night-forest.mp4" });
  await expect(detail.getByText("2:05")).toBeVisible();
  await expect(detail.getByText("1280 × 720")).toBeVisible();
  await expect(detail.getByText("films/night-forest.mp4")).toBeVisible();
  await expect(detail.getByText(IMPORT_ROOT)).toHaveCount(0);
  await page.keyboard.press("Escape");

  // Retention: +3 days.
  await page.getByRole("button", { name: "Actions · night-forest.mp4" }).click();
  await page.getByRole("menuitem", { name: "Extend retention" }).click();
  await page.getByRole("dialog", { name: "Extend retention" }).getByRole("button", { name: "+3 days" }).click();
  await expect(page.getByText("Retention extended.")).toBeVisible();

  // A Movie Review: the workflow is created and run at once.
  await page.getByRole("button", { name: "Actions · night-forest.mp4" }).click();
  await page.getByRole("menuitem", { name: "Use for Movie Review" }).click();
  await page.getByRole("dialog", { name: "Create a Movie Review" }).getByRole("button", { name: "Start" }).click();
  await expect(page).toHaveURL(/\/workflows\/[^?]+\?run=/);
  const runId = new URL(page.url()).searchParams.get("run")!;

  // While the run uses it, the source cannot be deleted.
  await page.goto("/media/movie-sources");
  await expect(row("night-forest.mp4").getByText("In use by a workflow")).toBeVisible();
  await page.getByRole("button", { name: "Actions · night-forest.mp4" }).click();
  await page.getByRole("menuitem", { name: "Delete now" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete now" }).click();
  await expect(page.getByText("A workflow run still uses this movie source; it cannot be deleted yet.")).toBeVisible();
  await page.keyboard.press("Escape");

  // The review runs to the human review step; its progress shows on the source.
  driver("pipeline", runId);
  await row("night-forest.mp4").getByRole("button", { name: /^night-forest\.mp4/ }).click();
  const progress = page.getByRole("list", { name: "Progress of the current run" });
  await expect(progress.getByText("Prepare movie")).toBeVisible();
  await expect(progress.getByText("Clip selector")).toBeVisible();
  // The final review appears: the Review step waits for a person.
  await expect(progress.getByRole("listitem").filter({ hasText: "Review" }).last().getByText("Awaiting review")).toBeVisible();
  await expect(progress.getByText(/\d+ \/ \d+ frames/)).toBeVisible();
  await page.keyboard.press("Escape");

  // Approved: the run completes and the source is marked used (deleted after its grace period).
  const approved = await owner.post(`/api/workflow-runs/${runId}/approve`);
  expect(approved.status(), await approved.text()).toBe(200);
  expect((await approved.json()).status).toBe("completed");
  await page.reload();
  await expect(row("night-forest.mp4").getByText("Used")).toBeVisible();

  // Delete now: scheduled, then removed from Drive by the worker.
  await page.getByRole("button", { name: "Actions · trailer.mp4" }).click();
  await page.getByRole("menuitem", { name: "Delete now" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete now" }).click();
  await expect(page.getByText("Deletion scheduled.")).toBeVisible();
  await expect(row("trailer.mp4").getByText("Deletion scheduled")).toBeVisible();
  driver("sources");
  await expect(row("trailer.mp4").getByText("Deleted")).toBeVisible({ timeout: 20_000 });

  // Admin → Operations: what the temporary Drive storage holds, from ReelForge's records.
  await page.context().clearCookies();
  await english(page.context());
  await asAdmin(page);
  // Admin → Overview: today's failed import (the redirect to an internal address) needs attention.
  await page.goto("/admin");
  await expect(page.getByText("Movie source imports failed in the last 24 hours: 1")).toBeVisible();
  await page.goto("/admin?tab=operations");
  const drive = page.getByTestId("movie-drive");
  await expect(drive.getByRole("heading", { name: "Movie source Drive" })).toBeVisible();
  await expect(drive.getByText("Oldest source")).toBeVisible();
  await expect(drive.getByText("Failing deletions")).toBeVisible();
  await owner.dispose();
});
