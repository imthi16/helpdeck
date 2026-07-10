import { expect, test } from "@playwright/test";

import { signUpAndSeedText } from "./helpers";

test("playground streams a cited answer and populates the debug panel", async ({ page }) => {
  await signUpAndSeedText(
    page,
    "Descaling Guide",
    "# Descaling\n\nDescale the machine every three months with normal use. " +
      "Use only food-safe descaling solution and never use vinegar.",
    "pg",
  );

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
