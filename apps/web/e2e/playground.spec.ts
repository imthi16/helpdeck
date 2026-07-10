import { expect, test, type Page } from "@playwright/test";

async function signUpAndSeed(page: Page): Promise<void> {
  const email = `pg-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
  await page.goto("/signup");
  await page.getByLabel("Your name").fill("PG User");
  await page.getByLabel("Organization name").fill("PG Coffee Co");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("supersecret1");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);

  // Seed a document so retrieval has grounded content.
  await page.goto("/dashboard/knowledge-base");
  await page.getByRole("tab", { name: "Paste text" }).click();
  await page.getByLabel("Title").fill("Descaling Guide");
  await page
    .getByLabel("Content")
    .fill(
      "# Descaling\n\nDescale the machine every three months with normal use. " +
        "Use only food-safe descaling solution and never use vinegar.",
    );
  await page.getByRole("button", { name: "Add text" }).click();
  const row = page.getByTestId("doc-row").filter({ hasText: "Descaling Guide" });
  await expect(row.getByTestId("doc-status")).toHaveText("ready", { timeout: 20_000 });
}

test("playground streams a cited answer and populates the debug panel", async ({ page }) => {
  await signUpAndSeed(page);
  await page.goto("/dashboard/playground");

  await page.getByTestId("chat-input").fill("How often should I descale?");
  await page.getByRole("button", { name: "Send" }).click();

  const assistant = page.getByTestId("assistant-message");
  await expect(assistant).toBeVisible();
  // Streamed answer contains at least one citation chip.
  await expect(assistant.getByTestId("citation-chip").first()).toBeVisible({ timeout: 20_000 });

  // Debug panel populated with retrieved chunks and confidence.
  await expect(page.getByTestId("debug-chunks").locator("li").first()).toBeVisible();
  await expect(page.getByTestId("debug-confidence")).not.toHaveText("—");
});
