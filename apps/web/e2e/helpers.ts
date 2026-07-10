import { expect, type Page } from "@playwright/test";

// Matches playwright.config.ts API_PORT.
const API_URL = "http://localhost:8020";

export function uniqueEmail(prefix = "e2e"): string {
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

/** Sign up a fresh user and complete onboarding, landing on the dashboard. */
export async function signUp(page: Page, prefix = "e2e"): Promise<string> {
  const email = uniqueEmail(prefix);
  await page.goto("/signup");
  await page.getByLabel("Your name").fill("E2E User");
  await page.getByLabel("Organization name").fill("E2E Coffee Co");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("supersecret1");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/onboarding$/);

  // Complete onboarding directly (the wizard flow is covered by its own spec).
  await page.evaluate(async (api) => {
    await fetch(`${api}/api/v1/onboarding/complete`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
  }, API_URL);

  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/dashboard$/);
  return email;
}

/** Sign up, then add a raw-text document and wait for it to finish ingesting. */
export async function signUpAndSeedText(
  page: Page,
  title: string,
  content: string,
  prefix = "e2e",
): Promise<string> {
  const email = await signUp(page, prefix);
  await page.goto("/dashboard/knowledge-base");
  await page.getByRole("tab", { name: "Paste text" }).click();
  await page.getByLabel("Title").fill(title);
  await page.getByLabel("Content").fill(content);
  await page.getByRole("button", { name: "Add text" }).click();
  await expect(
    page.getByTestId("doc-row").filter({ hasText: title }).getByTestId("doc-status"),
  ).toHaveText("ready", { timeout: 20_000 });
  return email;
}
