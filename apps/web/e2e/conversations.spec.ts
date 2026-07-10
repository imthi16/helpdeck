import { expect, test } from "@playwright/test";

import { signUpAndSeedText } from "./helpers";

test("an escalated chat appears in the inbox and can be resolved", async ({ page }) => {
  await signUpAndSeedText(page, "Descaling Guide", "# Descaling\n\nDescale every three months.", "conv");

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
