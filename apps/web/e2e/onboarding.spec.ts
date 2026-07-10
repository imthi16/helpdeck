import { expect, test } from "@playwright/test";

test("fresh signup is routed through the wizard exactly once", async ({ page }) => {
  const email = `onb-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;

  await page.goto("/signup");
  await page.getByLabel("Your name").fill("Onboarding User");
  await page.getByLabel("Organization name").fill("Onboard Coffee Co");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("supersecret1");
  await page.getByRole("button", { name: "Create account" }).click();

  // Fresh signup lands in the wizard, not the dashboard.
  await expect(page).toHaveURL(/\/onboarding$/);
  await expect(page.getByTestId("wizard-step")).toHaveText("Name your workspace");

  // Step 1 -> 2: workspace name is prefilled.
  await expect(page.getByLabel("Workspace name")).toHaveValue("Onboard Coffee Co");
  await page.getByRole("button", { name: "Continue" }).click();

  // Step 2: add the first document.
  await expect(page.getByTestId("wizard-step")).toHaveText("Add a document");
  await page.getByTestId("add-first-doc").click();

  // Step 3: ask a test question and get a streamed answer.
  await expect(page.getByTestId("wizard-step")).toHaveText("Ask a question");
  await page.getByTestId("ask-test").click();
  await expect(page.getByTestId("wizard-answer")).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Continue" }).click();

  // Step 4: embed snippet, then finish.
  await expect(page.getByTestId("embed-snippet")).toContainText("data-public-key");
  await page.getByTestId("finish-onboarding").click();
  await expect(page).toHaveURL(/\/dashboard$/);

  // The wizard does not run again: dashboard stays, and /onboarding redirects away.
  await page.reload();
  await expect(page).toHaveURL(/\/dashboard$/);
  await page.goto("/onboarding");
  await expect(page).toHaveURL(/\/dashboard$/);
});
