import { expect, test, type Page } from "@playwright/test";

async function signUpAndSeed(page: Page): Promise<void> {
  const email = `conv-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
  await page.goto("/signup");
  await page.getByLabel("Your name").fill("Conv User");
  await page.getByLabel("Organization name").fill("Conv Coffee Co");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("supersecret1");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);

  await page.goto("/dashboard/knowledge-base");
  await page.getByRole("tab", { name: "Paste text" }).click();
  await page.getByLabel("Title").fill("Descaling Guide");
  await page.getByLabel("Content").fill("# Descaling\n\nDescale every three months.");
  await page.getByRole("button", { name: "Add text" }).click();
  await expect(
    page.getByTestId("doc-row").filter({ hasText: "Descaling Guide" }).getByTestId("doc-status"),
  ).toHaveText("ready", { timeout: 20_000 });
}

test("an escalated chat appears in the inbox and can be resolved", async ({ page }) => {
  await signUpAndSeed(page);

  // Trigger an escalation with an out-of-KB question.
  await page.goto("/dashboard/playground");
  await page.getByTestId("chat-input").fill("What is your CEO's shoe size?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByTestId("assistant-message")).toBeVisible();
  // Wait for the turn to finish (input re-enabled).
  await expect(page.getByTestId("chat-input")).toBeEnabled({ timeout: 20_000 });

  // It shows up under Escalated.
  await page.goto("/dashboard/conversations");
  await page.getByRole("tab", { name: "Escalated" }).click();
  const row = page.getByTestId("conversation-row").first();
  await expect(row).toBeVisible({ timeout: 10_000 });

  // Open the transcript and resolve it.
  await row.click();
  await expect(page.getByTestId("transcript-status")).toHaveText("escalated");
  await page.getByTestId("resolve").click();
  await expect(page.getByTestId("transcript-status")).toHaveText("closed");

  // Internal reply is appended to the transcript.
  await page.getByTestId("reply-input").fill("Following up by email.");
  await page.getByRole("button", { name: "Reply" }).click();
  await expect(page.getByTestId("transcript")).toContainText("Following up by email.");
});
